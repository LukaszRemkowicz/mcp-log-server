from __future__ import annotations

from typing import Any

import pytest

from docker_socket_app import DockerSocketService, ProtocolException, dispatch_request


class FakeDockerBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def container_logs(
        self,
        *,
        container_name: str,
        since: str | None = None,
        until: str | None = None,
        tail: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "container_logs",
                {
                    "container_name": container_name,
                    "since": since,
                    "until": until,
                    "tail": tail,
                },
            )
        )
        return {"container_name": container_name, "logs": ["ready"], "truncated": False}

    def container_health(self, *, container_name: str) -> dict[str, Any]:
        self.calls.append(("container_health", {"container_name": container_name}))
        return {"container_name": container_name, "running": True}

    def traefik_router_tls_inventory(self) -> dict[str, Any]:
        self.calls.append(("traefik_router_tls_inventory", {}))
        return {"routers": [], "truncated": False}


def test_dispatch_request_rejects_unknown_services() -> None:
    service = DockerSocketService(backend=FakeDockerBackend())

    with pytest.raises(ProtocolException) as error:
        dispatch_request({"operation": "run_shell", "params": {}}, service)

    assert str(error.value) == "Unsupported docker socket operation: run_shell"


def test_dispatch_request_calls_fixed_log_operation() -> None:
    backend = FakeDockerBackend()
    service = DockerSocketService(backend=backend)

    response = dispatch_request(
        {
            "operation": "container_logs",
            "params": {
                "container_name": "portfolio-prod-be-1",
                "since": "2026-06-17T09:00:00Z",
                "until": "2026-06-17T10:00:00Z",
                "tail": 200,
            },
        },
        service,
    )

    assert response == {
        "ok": True,
        "result": {
            "container_name": "portfolio-prod-be-1",
            "logs": ["ready"],
            "truncated": False,
        },
    }
    assert backend.calls == [
        (
            "container_logs",
            {
                "container_name": "portfolio-prod-be-1",
                "since": "2026-06-17T09:00:00Z",
                "until": "2026-06-17T10:00:00Z",
                "tail": 200,
            },
        )
    ]


def test_dispatch_request_calls_fixed_traefik_router_tls_operation() -> None:
    backend = FakeDockerBackend()
    service = DockerSocketService(backend=backend)

    response = dispatch_request(
        {"operation": "traefik_router_tls_inventory", "params": {}},
        service,
    )

    assert response == {"ok": True, "result": {"routers": [], "truncated": False}}
    assert backend.calls == [("traefik_router_tls_inventory", {})]
