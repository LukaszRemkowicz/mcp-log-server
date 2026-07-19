from __future__ import annotations

from typing import Any

import pytest

from socket_app import ProtocolException, SocketOperationRegistry, dispatch_request


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

    def container_logs_page(
        self,
        *,
        transfer_id: str | None = None,
        container_name: str | None = None,
        stream: str | None = None,
        since: str | None = None,
        until: str | None = None,
        tail: int | None = None,
        offset: int = 0,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "container_logs_page",
                {
                    "transfer_id": transfer_id,
                    "container_name": container_name,
                    "stream": stream,
                    "since": since,
                    "until": until,
                    "tail": tail,
                    "offset": offset,
                    "max_bytes": max_bytes,
                },
            )
        )
        return {
            "transfer_id": "opaque-transfer" if offset == 0 else None,
            "container_name": container_name or "portfolio-prod-be-1",
            "logs_base64": "cmVhZHkK",
            "offset": offset,
            "returned_bytes": 6,
            "byte_limit": max_bytes,
            "truncated": True,
            "next_offset": offset + 6,
        }

    def container_health(self, *, container_name: str) -> dict[str, Any]:
        self.calls.append(("container_health", {"container_name": container_name}))
        return {"container_name": container_name, "running": True}

    def service_health(self) -> dict[str, Any]:
        self.calls.append(("service_health", {}))
        return {"status": "ok", "docker_reachable": True}

    def traefik_router_tls_inventory(self) -> dict[str, Any]:
        self.calls.append(("traefik_router_tls_inventory", {}))
        return {"routers": [], "truncated": False}

    def crowdsec_activity(self, *, container_name: str) -> dict[str, Any]:
        self.calls.append(("crowdsec_activity", {"container_name": container_name}))
        return {"container_name": container_name, "sections": {}}

    def landingpage_django_list_commands(
        self, *, container_name: str, base_command: list[str], cwd: str
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "landingpage_django_list_commands",
                {
                    "container_name": container_name,
                    "base_command": base_command,
                    "cwd": cwd,
                },
            )
        )
        return {"commands": []}

    def landingpage_django_media_inventory(
        self, *, container_name: str, base_command: list[str], cwd: str
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "landingpage_django_media_inventory",
                {
                    "container_name": container_name,
                    "base_command": base_command,
                    "cwd": cwd,
                },
            )
        )
        return {"summary": {"disk_files": 1}}


def test_dispatch_request_rejects_unknown_services() -> None:
    operation_registry = SocketOperationRegistry(backend=FakeDockerBackend())

    with pytest.raises(ProtocolException) as error:
        dispatch_request({"operation": "run_shell", "params": {}}, operation_registry)

    assert str(error.value) == "Unsupported docker socket operation: run_shell"


def test_dispatch_request_calls_service_health_without_params() -> None:
    backend = FakeDockerBackend()
    operation_registry = SocketOperationRegistry(backend=backend)

    response = dispatch_request(
        {"operation": "service_health", "params": {}},
        operation_registry,
    )

    assert response == {
        "ok": True,
        "result": {"status": "ok", "docker_reachable": True},
    }
    assert backend.calls == [("service_health", {})]


def test_dispatch_request_rejects_service_health_params() -> None:
    operation_registry = SocketOperationRegistry(backend=FakeDockerBackend())

    with pytest.raises(ProtocolException, match="does not accept parameters"):
        dispatch_request(
            {"operation": "service_health", "params": {"container_name": "app"}},
            operation_registry,
        )


def test_dispatch_request_calls_fixed_log_operation() -> None:
    backend = FakeDockerBackend()
    operation_registry = SocketOperationRegistry(backend=backend)

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
        operation_registry,
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


@pytest.mark.parametrize("stream", ["combined", "STDOUT", "", 1, True, []])
def test_dispatch_request_rejects_invalid_log_page_stream(stream: object) -> None:
    backend = FakeDockerBackend()
    operation_registry = SocketOperationRegistry(backend=backend)

    with pytest.raises(ProtocolException) as error:
        dispatch_request(
            {
                "operation": "container_logs_page",
                "params": {
                    "container_name": "portfolio-prod-be-1",
                    "stream": stream,
                },
            },
            operation_registry,
        )

    assert str(error.value) == "Parameter 'stream' must be 'stdout' or 'stderr'."
    assert backend.calls == []


@pytest.mark.parametrize("key", ["tail", "offset", "max_bytes", "max_entries"])
def test_optional_integer_parameters_reject_boolean_values(key: str) -> None:
    with pytest.raises(ProtocolException, match=f"Parameter '{key}' must be an integer"):
        SocketOperationRegistry._optional_int({key: True}, key)


def test_dispatch_request_calls_fixed_log_page_operation() -> None:
    backend = FakeDockerBackend()
    operation_registry = SocketOperationRegistry(backend=backend)

    response = dispatch_request(
        {
            "operation": "container_logs_page",
            "params": {
                "container_name": "portfolio-prod-be-1",
                "stream": "stderr",
                "since": "2026-06-17T09:00:00Z",
                "until": "2026-06-17T10:00:00Z",
                "tail": 200,
                "offset": 0,
                "max_bytes": 64,
            },
        },
        operation_registry,
    )

    assert response == {
        "ok": True,
        "result": {
            "transfer_id": "opaque-transfer",
            "container_name": "portfolio-prod-be-1",
            "logs_base64": "cmVhZHkK",
            "offset": 0,
            "returned_bytes": 6,
            "byte_limit": 64,
            "truncated": True,
            "next_offset": 6,
        },
    }
    assert backend.calls == [
        (
            "container_logs_page",
            {
                "transfer_id": None,
                "container_name": "portfolio-prod-be-1",
                "stream": "stderr",
                "since": "2026-06-17T09:00:00Z",
                "until": "2026-06-17T10:00:00Z",
                "tail": 200,
                "offset": 0,
                "max_bytes": 64,
            },
        )
    ]


def test_dispatch_request_calls_fixed_traefik_router_tls_operation() -> None:
    backend = FakeDockerBackend()
    operation_registry = SocketOperationRegistry(backend=backend)

    response = dispatch_request(
        {"operation": "traefik_router_tls_inventory", "params": {}},
        operation_registry,
    )

    assert response == {"ok": True, "result": {"routers": [], "truncated": False}}
    assert backend.calls == [("traefik_router_tls_inventory", {})]


def test_dispatch_request_calls_fixed_crowdsec_activity_operation() -> None:
    backend = FakeDockerBackend()
    operation_registry = SocketOperationRegistry(backend=backend)

    response = dispatch_request(
        {"operation": "crowdsec_activity", "params": {"container_name": "crowdsec"}},
        operation_registry,
    )

    assert response == {"ok": True, "result": {"container_name": "crowdsec", "sections": {}}}
    assert backend.calls == [("crowdsec_activity", {"container_name": "crowdsec"})]


def test_dispatch_request_calls_fixed_landingpage_django_operations() -> None:
    backend = FakeDockerBackend()
    operation_registry = SocketOperationRegistry(backend=backend)

    commands_response = dispatch_request(
        {
            "operation": "landingpage_django_list_commands",
            "params": {
                "container_name": "portfolio-prod-be-1",
                "base_command": ["uv", "run", "python", "manage.py"],
                "cwd": "/app",
            },
        },
        operation_registry,
    )
    inventory_response = dispatch_request(
        {
            "operation": "landingpage_django_media_inventory",
            "params": {
                "container_name": "portfolio-prod-be-1",
                "base_command": ["uv", "run", "python", "manage.py"],
                "cwd": "/app",
            },
        },
        operation_registry,
    )

    assert commands_response == {"ok": True, "result": {"commands": []}}
    assert inventory_response == {"ok": True, "result": {"summary": {"disk_files": 1}}}
    assert backend.calls == [
        (
            "landingpage_django_list_commands",
            {
                "container_name": "portfolio-prod-be-1",
                "base_command": ["uv", "run", "python", "manage.py"],
                "cwd": "/app",
            },
        ),
        (
            "landingpage_django_media_inventory",
            {
                "container_name": "portfolio-prod-be-1",
                "base_command": ["uv", "run", "python", "manage.py"],
                "cwd": "/app",
            },
        ),
    ]
