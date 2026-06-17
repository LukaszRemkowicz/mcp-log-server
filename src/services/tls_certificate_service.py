"""TLS certificate inspection service for configured public domains."""

from __future__ import annotations

import ipaddress
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from conf import settings

TLS_CERTIFICATE_PORT = 443
DEFAULT_TLS_CERTIFICATE_HOST_LIMIT = 8
TlsInspectionStatus = Literal["ok", "warning", "unavailable"]
TlsDomainKey = Literal["site", "site_subdomain"]
TlsWarningLevel = Literal[
    "ok",
    "expired",
    "expiring_soon",
    "hostname_mismatch",
    "connection_failure",
    "unsupported_tls_response",
    "configuration_error",
]
TlsCertificateNameAttribute = tuple[str, str]
TlsCertificateRelativeDistinguishedName = tuple[TlsCertificateNameAttribute, ...]
TlsCertificateName = tuple[TlsCertificateRelativeDistinguishedName, ...]


@dataclass(frozen=True, slots=True)
class TlsCertificateData:
    """Parsed TLS certificate facts used by the inspection classifier."""

    subject_summary: str
    issuer_summary: str
    not_before: datetime
    not_after: datetime
    san_names: list[str]


@dataclass(frozen=True, slots=True)
class TlsCertificateInspection:
    """Agent-facing TLS certificate inspection result."""

    domain_key: TlsDomainKey
    hostname: str | None
    port: int
    inspection_status: TlsInspectionStatus
    warning_level: TlsWarningLevel
    subject_summary: str | None
    issuer_summary: str | None
    not_before: str | None
    not_after: str | None
    days_until_expiry: int | None
    hostname_matches: bool | None
    matched_names: list[str]
    error_code: str | None
    message: str


FetchCertificate = Callable[[str, int, float], TlsCertificateData]


class TlsCertificateService:
    """Inspect the configured SITE_DOMAIN certificate with bounded network behavior."""

    def __init__(
        self,
        fetch_certificate: FetchCertificate | None = None,
    ) -> None:
        self._fetch_certificate = fetch_certificate or self._fetch_certificate_from_network

    def inspect_certificates(self) -> list[TlsCertificateInspection]:
        """Return certificate status for SITE_DOMAIN and approved subdomains."""

        hostname = settings.SITE_DOMAIN.strip()
        port = TLS_CERTIFICATE_PORT
        timeout = settings.TLS_CERTIFICATE_TIMEOUT_SECONDS
        warning_days = settings.TLS_CERTIFICATE_EXPIRY_WARNING_DAYS

        if not hostname:
            return [
                self._configuration_error(
                    hostname=None,
                    port=port,
                    error_code="tls_site_domain_not_configured",
                    message="SITE_DOMAIN is not configured.",
                )
            ]
        if self._hostname_is_denied(hostname):
            return [
                self._configuration_error(
                    hostname=hostname,
                    port=port,
                    error_code="tls_site_domain_invalid",
                    message=(
                        "SITE_DOMAIN must be a public domain name without scheme, path, or port."
                    ),
                )
            ]

        normalized_hostnames = self._configured_hostnames(hostname)
        inspections: list[TlsCertificateInspection] = []
        for normalized_hostname in normalized_hostnames:
            if not self._hostname_is_allowed_for_site(hostname.lower(), normalized_hostname):
                inspections.append(
                    self._configuration_error(
                        domain_key="site_subdomain",
                        hostname=normalized_hostname,
                        port=port,
                        error_code="tls_subdomain_invalid",
                        message=(
                            "TLS certificate subdomains must be names that expand under "
                            "SITE_DOMAIN."
                        ),
                    )
                )
                continue
            domain_key: TlsDomainKey = (
                "site" if normalized_hostname == hostname.lower() else "site_subdomain"
            )
            inspections.append(
                self._inspect_certificate(
                    domain_key=domain_key,
                    hostname=normalized_hostname,
                    port=port,
                    timeout=timeout,
                    warning_days=warning_days,
                )
            )
        return inspections

    def _inspect_certificate(
        self,
        *,
        domain_key: TlsDomainKey,
        hostname: str,
        port: int,
        timeout: float,
        warning_days: int,
    ) -> TlsCertificateInspection:
        """Inspect one already-normalized hostname."""

        try:
            certificate = self._fetch_certificate(hostname, port, timeout)
        except (TimeoutError, OSError, ssl.SSLError) as exc:
            return TlsCertificateInspection(
                domain_key=domain_key,
                hostname=hostname,
                port=port,
                inspection_status="unavailable",
                warning_level="connection_failure",
                subject_summary=None,
                issuer_summary=None,
                not_before=None,
                not_after=None,
                days_until_expiry=None,
                hostname_matches=None,
                matched_names=[],
                error_code="tls_certificate_connection_failed",
                message=f"Could not inspect TLS certificate for SITE_DOMAIN: {exc}",
            )

        now = datetime.now(UTC)
        days_until_expiry = (certificate.not_after - now).days
        hostname_matches = self._certificate_matches_hostname(certificate, hostname)
        if not hostname_matches:
            inspection_status: TlsInspectionStatus = "warning"
            warning_level: TlsWarningLevel = "hostname_mismatch"
            message = "TLS certificate SANs do not match SITE_DOMAIN."
        elif certificate.not_after < now:
            inspection_status = "warning"
            warning_level = "expired"
            message = "TLS certificate for SITE_DOMAIN is expired."
        elif days_until_expiry <= warning_days:
            inspection_status = "warning"
            warning_level = "expiring_soon"
            message = "TLS certificate for SITE_DOMAIN is nearing expiry."
        else:
            inspection_status = "ok"
            warning_level = "ok"
            message = "TLS certificate is valid for SITE_DOMAIN."

        return TlsCertificateInspection(
            domain_key=domain_key,
            hostname=hostname,
            port=port,
            inspection_status=inspection_status,
            warning_level=warning_level,
            subject_summary=certificate.subject_summary,
            issuer_summary=certificate.issuer_summary,
            not_before=certificate.not_before.isoformat(),
            not_after=certificate.not_after.isoformat(),
            days_until_expiry=days_until_expiry,
            hostname_matches=hostname_matches,
            matched_names=certificate.san_names,
            error_code=None,
            message=message,
        )

    def _configured_hostnames(self, site_domain: str) -> list[str]:
        """Return SITE_DOMAIN plus configured TLS subdomains expanded to FQDNs."""

        site_domain = site_domain.lower()
        subdomains = getattr(settings, "TLS_CERTIFICATE_SUBDOMAINS", [])
        raw_hostnames = [site_domain, *subdomains]
        normalized_hostnames: list[str] = []
        seen_hostnames: set[str] = set()
        for raw_hostname in raw_hostnames[:DEFAULT_TLS_CERTIFICATE_HOST_LIMIT]:
            normalized_hostname = self._normalize_one_configured_hostname(
                site_domain, str(raw_hostname)
            )
            if normalized_hostname in seen_hostnames:
                continue
            seen_hostnames.add(normalized_hostname)
            normalized_hostnames.append(normalized_hostname)
        return normalized_hostnames or [site_domain]

    @staticmethod
    def _normalize_one_configured_hostname(site_domain: str, raw_hostname: str) -> str:
        """Return one normalized configured SITE_DOMAIN or subdomain candidate."""

        hostname = raw_hostname.strip().lower().rstrip(".")
        if hostname != site_domain and "." not in hostname:
            hostname = f"{hostname}.{site_domain}"
        return hostname

    def _hostname_is_allowed_for_site(self, site_domain: str, hostname: str) -> bool:
        """Return whether hostname can be inspected by this bounded tool."""

        if self._hostname_is_denied(hostname):
            return False
        return hostname == site_domain or hostname.endswith(f".{site_domain}")

    @staticmethod
    def _configuration_error(
        hostname: str | None,
        port: int,
        error_code: str,
        message: str,
        domain_key: TlsDomainKey = "site",
    ) -> TlsCertificateInspection:
        """Return one configuration failure without opening a network connection."""

        return TlsCertificateInspection(
            domain_key=domain_key,
            hostname=hostname,
            port=port,
            inspection_status="unavailable",
            warning_level="configuration_error",
            subject_summary=None,
            issuer_summary=None,
            not_before=None,
            not_after=None,
            days_until_expiry=None,
            hostname_matches=None,
            matched_names=[],
            error_code=error_code,
            message=message,
        )

    def _fetch_certificate_from_network(
        self,
        hostname: str,
        port: int,
        timeout: float,
    ) -> TlsCertificateData:
        """Open one bounded TLS connection and parse the peer certificate."""

        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        with socket.create_connection((hostname, port), timeout=timeout) as raw_socket:
            with context.wrap_socket(raw_socket, server_hostname=hostname) as tls_socket:
                certificate = tls_socket.getpeercert()
        if not certificate:
            raise ssl.SSLError("TLS peer did not return a certificate.")
        return self._parse_certificate(certificate)

    def _parse_certificate(self, certificate: dict[str, Any]) -> TlsCertificateData:
        """Parse the stdlib ssl certificate dictionary into stable service data."""

        not_before_value = certificate.get("notBefore")
        not_after_value = certificate.get("notAfter")
        if not isinstance(not_before_value, str) or not isinstance(not_after_value, str):
            raise ssl.SSLError("TLS certificate response did not include validity timestamps.")

        san_names = [
            value
            for kind, value in certificate.get("subjectAltName", ())
            if kind == "DNS" and isinstance(value, str)
        ]
        return TlsCertificateData(
            subject_summary=self._name_summary(
                cast(TlsCertificateName, certificate.get("subject", ()))
            ),
            issuer_summary=self._name_summary(
                cast(TlsCertificateName, certificate.get("issuer", ()))
            ),
            not_before=self._parse_ssl_timestamp(not_before_value),
            not_after=self._parse_ssl_timestamp(not_after_value),
            san_names=san_names,
        )

    @staticmethod
    def _parse_ssl_timestamp(value: str) -> datetime:
        """Parse stdlib ssl timestamp strings as UTC datetimes."""

        return datetime.strptime(value, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)

    @staticmethod
    def _name_summary(value: TlsCertificateName) -> str:
        """Summarize one certificate subject/issuer tuple as key=value pairs."""

        pairs = [
            f"{key}={value}"
            for relative_distinguished_name in value
            for key, value in relative_distinguished_name
        ]
        return ", ".join(pairs)

    @staticmethod
    def _certificate_matches_hostname(
        certificate: TlsCertificateData,
        hostname: str,
    ) -> bool:
        """Return whether any DNS SAN matches the configured hostname."""

        hostname = hostname.lower()
        for san_name in certificate.san_names:
            candidate = san_name.lower()
            if candidate == hostname:
                return True
            if candidate.startswith("*.") and hostname.endswith(candidate[1:]):
                return True
        return False

    @staticmethod
    def _hostname_is_denied(hostname: str) -> bool:
        """Reject settings values that would turn the tool into a scanner."""

        if any(item in hostname for item in ("/", "\\", ":", "*", " ")):
            return True
        try:
            ipaddress.ip_address(hostname)
            return True
        except ValueError:
            return False
