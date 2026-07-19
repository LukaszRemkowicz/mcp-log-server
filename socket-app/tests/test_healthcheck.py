from __future__ import annotations

from collections.abc import Buffer
from io import BytesIO
from pathlib import Path

import pytest

from socket_app.healthcheck import HealthcheckError, check_service_health


class FakeSocket:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.connected_path: str | None = None
        self.sent = b""
        self.timeout: float | None = None

    def __enter__(self) -> FakeSocket:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, path: str) -> None:
        self.connected_path = path

    def sendall(self, payload: Buffer) -> None:
        self.sent += bytes(payload)

    def makefile(self, mode: str) -> BytesIO:
        assert mode == "rb"
        return BytesIO(self.response)


def test_healthcheck_calls_service_health_over_the_unix_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeSocket(b'{"ok":true,"result":{"status":"ok","docker_reachable":true}}\n')
    monkeypatch.setattr("socket_app.healthcheck.socket.socket", lambda *_: client)

    check_service_health(Path("/run/socket-app/gateway.sock"), timeout_seconds=2.5)

    assert client.connected_path == "/run/socket-app/gateway.sock"
    assert client.timeout == 2.5
    assert client.sent == b'{"operation":"service_health","params":{}}\n'


@pytest.mark.parametrize(
    "response",
    [
        b'{"ok":false,"error":{"message":"Docker daemon unavailable"}}\n',
        b'{"ok":true,"result":{"status":"degraded","docker_reachable":false}}\n',
        b"not-json\n",
        b"",
    ],
)
def test_healthcheck_rejects_unhealthy_or_invalid_responses(
    monkeypatch: pytest.MonkeyPatch,
    response: bytes,
) -> None:
    monkeypatch.setattr("socket_app.healthcheck.socket.socket", lambda *_: FakeSocket(response))

    with pytest.raises(HealthcheckError):
        check_service_health(Path("/run/socket-app/gateway.sock"), timeout_seconds=2.5)
