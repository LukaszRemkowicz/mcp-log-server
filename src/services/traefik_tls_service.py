"""Traefik runtime TLS inspection service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from services.docker_socket_gateway import DockerSocketGatewayClient, DockerSocketGatewayError

TraefikCertificateSource = Literal["acme_resolver", "static_or_default", "not_tls"]


class DockerSocketClientProtocol(Protocol):
    """Fixed-operation socket client contract used by Traefik TLS inspection."""

    def request(self, operation: str, params: Mapping[str, object]) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class TraefikRouterTlsInspection:
    """Sanitized TLS facts for one Traefik HTTP router."""

    router_name: str
    container_name: str
    rule: str | None
    entrypoints: list[str]
    service: str | None
    tls_enabled: bool
    cert_resolver: str | None
    certificate_source: TraefikCertificateSource


@dataclass(frozen=True, slots=True)
class TraefikTlsInspectionResult:
    """Sanitized Traefik router TLS inspection result."""

    routers: list[TraefikRouterTlsInspection]
    truncated: bool


@dataclass(frozen=True, slots=True)
class TraefikTlsInspectionError:
    """Expected Traefik TLS inspection failure."""

    message: str
    error_code: str


class TraefikTlsService:
    """Read sanitized Traefik TLS metadata through the Docker socket gateway."""

    def __init__(self, docker_socket_client: DockerSocketClientProtocol | None = None) -> None:
        self.docker_socket_client = docker_socket_client or DockerSocketGatewayClient()

    def inspect_router_tls(self) -> TraefikTlsInspectionResult | TraefikTlsInspectionError:
        """Return sanitized Traefik router TLS facts or a typed error."""

        try:
            payload = self.docker_socket_client.request("traefik_router_tls_inventory", {})
        except DockerSocketGatewayError as error:
            return TraefikTlsInspectionError(message=error.message, error_code=error.error_code)

        raw_routers = payload.get("routers")
        if not isinstance(raw_routers, list):
            return TraefikTlsInspectionError(
                message="Docker socket app returned invalid Traefik router inventory.",
                error_code="invalid_traefik_router_inventory",
            )
        routers: list[TraefikRouterTlsInspection] = []
        for item in raw_routers:
            if not isinstance(item, dict):
                continue
            parsed = self._parse_router(item)
            if parsed is not None:
                routers.append(parsed)
        return TraefikTlsInspectionResult(
            routers=routers,
            truncated=payload.get("truncated") is True,
        )

    @staticmethod
    def _parse_router(item: dict[str, Any]) -> TraefikRouterTlsInspection | None:
        router_name = item.get("router_name")
        container_name = item.get("container_name")
        tls_enabled = item.get("tls_enabled")
        certificate_source = item.get("certificate_source")
        entrypoints = item.get("entrypoints")
        if (
            not isinstance(router_name, str)
            or not router_name
            or not isinstance(container_name, str)
            or not isinstance(tls_enabled, bool)
            or certificate_source not in {"acme_resolver", "static_or_default", "not_tls"}
            or not isinstance(entrypoints, list)
        ):
            return None
        return TraefikRouterTlsInspection(
            router_name=router_name,
            container_name=container_name,
            rule=_optional_string(item.get("rule")),
            entrypoints=[str(entrypoint) for entrypoint in entrypoints if entrypoint is not None],
            service=_optional_string(item.get("service")),
            tls_enabled=tls_enabled,
            cert_resolver=_optional_string(item.get("cert_resolver")),
            certificate_source=certificate_source,
        )


def _optional_string(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)
