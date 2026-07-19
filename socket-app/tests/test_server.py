from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any
from uuid import uuid4

import pytest

from socket_app import DockerSocketServer, SocketOperationRegistry
from socket_app import server as server_module
from socket_app.exceptions import DockerBackendError


class RecordingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def debug(self, event: str, *, extra: dict[str, object] | None = None) -> None:
        self.calls.append(("debug", event, extra or {}))

    def info(self, event: str, *, extra: dict[str, object] | None = None) -> None:
        self.calls.append(("info", event, extra or {}))

    def error(self, event: str, *, extra: dict[str, object] | None = None) -> None:
        self.calls.append(("error", event, extra or {}))


class FakeDockerBackend:
    def container_health(self, *, container_name: str) -> dict[str, Any]:
        return {"container_name": container_name, "running": True}

    def crowdsec_activity(self, *, container_name: str) -> dict[str, Any]:
        return {"container_name": container_name, "sections": {}}

    def service_health(self) -> dict[str, Any]:
        return {"status": "ok", "docker_reachable": True}


class FailingDockerBackend(FakeDockerBackend):
    def service_health(self) -> dict[str, Any]:
        raise DockerBackendError("secret daemon endpoint rejected credentials")


@pytest.mark.asyncio
async def test_unix_socket_server_handles_one_json_line_request() -> None:
    socket_path = Path("/tmp") / f"socket-app-{uuid4().hex}.sock"
    operation_registry = SocketOperationRegistry(backend=FakeDockerBackend())
    server = DockerSocketServer(
        socket_path=socket_path,
        operation_registry=operation_registry,
    )

    async with server.running():
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        writer.write(
            b'{"operation":"container_health","params":{"container_name":"portfolio-prod-be-1"}}\n'
        )
        await writer.drain()

        raw_response = await reader.readline()
        writer.close()
        await writer.wait_closed()

    assert raw_response == (
        b'{"ok":true,"result":{"container_name":"portfolio-prod-be-1","running":true}}\n'
    )


class BlockingDockerBackend(FakeDockerBackend):
    def __init__(self) -> None:
        self.blocking_request_started = Event()
        self.release_blocking_request = Event()

    def container_health(self, *, container_name: str) -> dict[str, Any]:
        self.blocking_request_started.set()
        self.release_blocking_request.wait(timeout=2)
        return super().container_health(container_name=container_name)


class MemoryWriter:
    def __init__(self) -> None:
        self.output = bytearray()

    def write(self, value: bytes) -> None:
        self.output.extend(value)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


def request_reader(raw_request: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(raw_request)
    reader.feed_eof()
    return reader


@pytest.mark.asyncio
async def test_service_health_remains_responsive_during_blocking_docker_request() -> None:
    backend = BlockingDockerBackend()
    server = DockerSocketServer(
        socket_path=Path("/tmp/unused.sock"),
        operation_registry=SocketOperationRegistry(backend=backend),
    )
    slow_writer = MemoryWriter()
    request_started_at = monotonic()
    slow_task = asyncio.create_task(
        server._handle_client(
            request_reader(
                b'{"operation":"container_health","params":{"container_name":"slow"}}\n'
            ),
            slow_writer,  # type: ignore[arg-type]
        )
    )
    await asyncio.to_thread(backend.blocking_request_started.wait, 1)

    health_writer = MemoryWriter()
    await asyncio.wait_for(
        server._handle_client(
            request_reader(b'{"operation":"service_health","params":{}}\n'),
            health_writer,  # type: ignore[arg-type]
        ),
        timeout=0.25,
    )

    response_duration = monotonic() - request_started_at
    backend.release_blocking_request.set()
    await asyncio.wait_for(slow_task, timeout=1)
    assert response_duration < 0.5
    assert bytes(health_writer.output) == (
        b'{"ok":true,"result":{"status":"ok","docker_reachable":true}}\n'
    )


def test_service_health_success_logs_at_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    recording_logger = RecordingLogger()
    monkeypatch.setattr(server_module, "logger", recording_logger)
    server = DockerSocketServer(
        socket_path=Path("/tmp/socket-app.sock"),
        operation_registry=SocketOperationRegistry(backend=FakeDockerBackend()),
    )

    response = server._build_response(b'{"operation":"service_health","params":{}}\n')

    assert response == b'{"ok":true,"result":{"status":"ok","docker_reachable":true}}\n'
    assert recording_logger.calls[0] == ("debug", "socket_service_health_completed", {})


def test_non_health_success_logs_at_info(monkeypatch: pytest.MonkeyPatch) -> None:
    recording_logger = RecordingLogger()
    monkeypatch.setattr(server_module, "logger", recording_logger)
    server = DockerSocketServer(
        socket_path=Path("/tmp/socket-app.sock"),
        operation_registry=SocketOperationRegistry(backend=FakeDockerBackend()),
    )

    server._build_response(b'{"operation":"container_health","params":{"container_name":"app"}}\n')

    assert recording_logger.calls[0] == ("info", "socket_request_completed", {})


def test_backend_failure_logs_sanitized_error_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    recording_logger = RecordingLogger()
    monkeypatch.setattr(server_module, "logger", recording_logger)
    server = DockerSocketServer(
        socket_path=Path("/tmp/socket-app.sock"),
        operation_registry=SocketOperationRegistry(backend=FailingDockerBackend()),
    )

    server._build_response(b'{"operation":"service_health","params":{}}\n')

    assert recording_logger.calls[0] == ("error", "socket_request_failed_docker_backend", {})
    assert "secret" not in repr(recording_logger.calls)
    assert "credentials" not in repr(recording_logger.calls)


def test_log_request_outcome_sanitizes_operation_at_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording_logger = RecordingLogger()
    monkeypatch.setattr(server_module, "logger", recording_logger)
    secret_operation = "Bearer-secret-token-" * 10_000

    DockerSocketServer._log_request_outcome(
        operation=secret_operation,
        unsupported_operation=False,
        ok=True,
        request_error=None,
        duration_ms=1.0,
    )

    assert recording_logger.calls[0] == ("info", "socket_request_completed", {})
    assert secret_operation not in repr(recording_logger.calls)


def test_unknown_operation_is_logged_as_fixed_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    recording_logger = RecordingLogger()
    monkeypatch.setattr(server_module, "logger", recording_logger)
    server = DockerSocketServer(
        socket_path=Path("/tmp/socket-app.sock"),
        operation_registry=SocketOperationRegistry(backend=FakeDockerBackend()),
    )
    secret_operation = "Bearer-secret-token-" * 10_000
    raw_request = ('{"operation":"' + secret_operation + '","params":{}}\n').encode()

    server._build_response(raw_request)

    assert recording_logger.calls[0] == (
        "error",
        "socket_request_failed_unsupported_operation",
        {},
    )
    assert secret_operation not in repr(recording_logger.calls)
