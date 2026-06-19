from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.traefik_tls_service import TraefikTlsInspectionResult, TraefikTlsService


class FakeDockerSocketClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, object]]] = []

    def request(self, operation: str, params: Mapping[str, object]) -> dict[str, Any]:
        self.calls.append((operation, dict(params)))
        return self.payload


def test_traefik_tls_service_parses_sanitized_router_inventory() -> None:
    client = FakeDockerSocketClient(
        {
            "routers": [
                {
                    "router_name": "portfolio-prod",
                    "container_name": "portfolio-prod-nginx-1",
                    "rule": "Host(`example.com`)",
                    "entrypoints": ["websecure"],
                    "service": "portfolio-prod",
                    "tls_enabled": True,
                    "cert_resolver": "letsencrypt",
                    "certificate_source": "acme_resolver",
                    "labels": {"traefik.http.middlewares.secret": "hidden"},
                }
            ],
            "truncated": False,
        }
    )
    service = TraefikTlsService(docker_socket_client=client)

    result = service.inspect_router_tls()

    assert isinstance(result, TraefikTlsInspectionResult)
    assert client.calls == [("traefik_router_tls_inventory", {})]
    assert result.truncated is False
    assert len(result.routers) == 1
    router = result.routers[0]
    assert router.router_name == "portfolio-prod"
    assert router.cert_resolver == "letsencrypt"
    assert router.certificate_source == "acme_resolver"
