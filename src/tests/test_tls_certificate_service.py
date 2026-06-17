from __future__ import annotations

import ssl
from datetime import UTC, datetime, timedelta
from inspect import signature
from types import TracebackType

from services.tls_certificate_service import TlsCertificateData, TlsCertificateService
from tests.conftest import override_settings


def _certificate(
    *,
    hostname: str = "example.com",
    not_after: datetime | None = None,
    san_names: list[str] | None = None,
) -> TlsCertificateData:
    now = datetime.now(UTC)
    return TlsCertificateData(
        subject_summary="CN=example.com",
        issuer_summary="CN=Example CA",
        not_before=now - timedelta(days=30),
        not_after=not_after or now + timedelta(days=90),
        san_names=san_names or [hostname],
    )


def test_tls_certificate_service_reports_valid_certificate() -> None:
    with override_settings(
        SITE_DOMAIN="example.com",
        TLS_CERTIFICATE_TIMEOUT_SECONDS=5,
        TLS_CERTIFICATE_EXPIRY_WARNING_DAYS=30,
    ):
        service = TlsCertificateService(
            fetch_certificate=lambda _hostname, _port, _timeout: _certificate(
                not_after=datetime.now(UTC) + timedelta(days=91)
            ),
        )

        result = service.inspect_certificates()[0]

    assert result.domain_key == "site"
    assert result.hostname == "example.com"
    assert result.port == 443
    assert result.inspection_status == "ok"
    assert result.warning_level == "ok"
    assert result.days_until_expiry is not None
    assert result.days_until_expiry >= 89
    assert result.hostname_matches is True
    assert result.matched_names == ["example.com"]
    assert result.error_code is None


def test_tls_certificate_service_inspects_site_domain_and_configured_subdomains() -> None:
    requested_hostnames: list[str] = []

    def fetch_certificate(hostname: str, _port: int, _timeout: float) -> TlsCertificateData:
        requested_hostnames.append(hostname)
        return _certificate(hostname=hostname)

    with override_settings(
        SITE_DOMAIN="example.com",
        TLS_CERTIFICATE_SUBDOMAINS=["admin", "stage", "mcp"],
        TLS_CERTIFICATE_TIMEOUT_SECONDS=5,
        TLS_CERTIFICATE_EXPIRY_WARNING_DAYS=30,
    ):
        service = TlsCertificateService(fetch_certificate=fetch_certificate)

        results = service.inspect_certificates()

    assert requested_hostnames == [
        "example.com",
        "admin.example.com",
        "stage.example.com",
        "mcp.example.com",
    ]
    assert [result.hostname for result in results] == [
        "example.com",
        "admin.example.com",
        "stage.example.com",
        "mcp.example.com",
    ]
    assert [result.domain_key for result in results] == [
        "site",
        "site_subdomain",
        "site_subdomain",
        "site_subdomain",
    ]
    assert all(result.inspection_status == "ok" for result in results)


def test_tls_certificate_service_rejects_invalid_configured_subdomains() -> None:
    with override_settings(
        SITE_DOMAIN="example.com",
        TLS_CERTIFICATE_SUBDOMAINS=["admin", "https://evil.test"],
        TLS_CERTIFICATE_TIMEOUT_SECONDS=5,
        TLS_CERTIFICATE_EXPIRY_WARNING_DAYS=30,
    ):
        service = TlsCertificateService()

        results = service.inspect_certificates()

    assert results[0].hostname == "example.com"
    assert results[1].hostname == "admin.example.com"
    assert results[2].hostname == "https://evil.test"
    assert results[2].inspection_status == "unavailable"
    assert results[2].warning_level == "configuration_error"
    assert results[2].error_code == "tls_subdomain_invalid"


def test_tls_certificate_service_warns_for_expiring_certificate() -> None:
    with override_settings(
        SITE_DOMAIN="example.com",
        TLS_CERTIFICATE_TIMEOUT_SECONDS=5,
        TLS_CERTIFICATE_EXPIRY_WARNING_DAYS=30,
    ):
        service = TlsCertificateService(
            fetch_certificate=lambda _hostname, _port, _timeout: _certificate(
                not_after=datetime.now(UTC) + timedelta(days=8)
            ),
        )

        result = service.inspect_certificates()[0]

    assert result.inspection_status == "warning"
    assert result.warning_level == "expiring_soon"
    assert result.days_until_expiry is not None
    assert 6 <= result.days_until_expiry <= 8


def test_tls_certificate_service_warns_for_expired_certificate() -> None:
    with override_settings(
        SITE_DOMAIN="example.com",
        TLS_CERTIFICATE_TIMEOUT_SECONDS=5,
        TLS_CERTIFICATE_EXPIRY_WARNING_DAYS=30,
    ):
        service = TlsCertificateService(
            fetch_certificate=lambda _hostname, _port, _timeout: _certificate(
                not_after=datetime.now(UTC) - timedelta(hours=1)
            ),
        )

        result = service.inspect_certificates()[0]

    assert result.inspection_status == "warning"
    assert result.warning_level == "expired"
    assert result.days_until_expiry is not None
    assert result.days_until_expiry < 0


def test_tls_certificate_service_warns_for_hostname_mismatch() -> None:
    with override_settings(
        SITE_DOMAIN="example.com",
        TLS_CERTIFICATE_TIMEOUT_SECONDS=5,
        TLS_CERTIFICATE_EXPIRY_WARNING_DAYS=30,
    ):
        service = TlsCertificateService(
            fetch_certificate=lambda _hostname, _port, _timeout: _certificate(
                san_names=["other.example"]
            ),
        )

        result = service.inspect_certificates()[0]

    assert result.inspection_status == "warning"
    assert result.warning_level == "hostname_mismatch"
    assert result.hostname_matches is False
    assert result.matched_names == ["other.example"]


def test_tls_certificate_service_reports_connection_failure() -> None:
    def raise_timeout(_hostname: str, _port: int, _timeout: float) -> TlsCertificateData:
        raise TimeoutError("timed out")

    with override_settings(
        SITE_DOMAIN="example.com",
        TLS_CERTIFICATE_TIMEOUT_SECONDS=5,
        TLS_CERTIFICATE_EXPIRY_WARNING_DAYS=30,
    ):
        service = TlsCertificateService(fetch_certificate=raise_timeout)

        result = service.inspect_certificates()[0]

    assert result.inspection_status == "unavailable"
    assert result.warning_level == "connection_failure"
    assert result.error_code == "tls_certificate_connection_failed"
    assert "timed out" in result.message


def test_tls_certificate_network_fetch_requires_tls_1_2_or_newer(monkeypatch) -> None:
    class FakeTlsSocket:
        def __enter__(self) -> FakeTlsSocket:
            return self

        def __exit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: TracebackType | None,
        ) -> None:
            return None

        def getpeercert(self) -> dict[str, object]:
            return {
                "subject": ((("commonName", "example.com"),),),
                "issuer": ((("commonName", "Example CA"),),),
                "notBefore": "Jan  1 00:00:00 2026 GMT",
                "notAfter": "Apr  1 00:00:00 2026 GMT",
                "subjectAltName": (("DNS", "example.com"),),
            }

    class FakeContext:
        minimum_version: ssl.TLSVersion | None = None

        def wrap_socket(self, _raw_socket: object, *, server_hostname: str) -> FakeTlsSocket:
            assert server_hostname == "example.com"
            return FakeTlsSocket()

    class FakeRawSocket:
        def __enter__(self) -> FakeRawSocket:
            return self

        def __exit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: TracebackType | None,
        ) -> None:
            return None

    fake_context = FakeContext()
    monkeypatch.setattr(
        "services.tls_certificate_service.ssl.create_default_context",
        lambda: fake_context,
    )
    monkeypatch.setattr(
        "services.tls_certificate_service.socket.create_connection",
        lambda _target, *, timeout: FakeRawSocket(),
    )

    TlsCertificateService()._fetch_certificate_from_network("example.com", 443, 5)

    assert fake_context.minimum_version == ssl.TLSVersion.TLSv1_2


def test_tls_certificate_service_rejects_unconfigured_site_domain() -> None:
    with override_settings(
        SITE_DOMAIN="",
        TLS_CERTIFICATE_TIMEOUT_SECONDS=5,
        TLS_CERTIFICATE_EXPIRY_WARNING_DAYS=30,
    ):
        service = TlsCertificateService()

        result = service.inspect_certificates()[0]

    assert result.inspection_status == "unavailable"
    assert result.warning_level == "configuration_error"
    assert result.error_code == "tls_site_domain_not_configured"


def test_tls_certificate_service_constructor_does_not_accept_settings() -> None:
    constructor_signature = signature(TlsCertificateService)

    assert "settings" not in constructor_signature.parameters
