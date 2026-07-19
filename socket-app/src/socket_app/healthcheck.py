"""Container health probe for the Unix-socket Docker gateway."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from .settings import SOCKET_APP_HEALTHCHECK_TIMEOUT_SECONDS, SOCKET_APP_SOCKET_PATH

_HEALTH_REQUEST = memoryview(b'{"operation":"service_health","params":{}}\n')


class HealthcheckError(RuntimeError):
    """Raised when the socket app or Docker daemon is not ready."""


def check_service_health(socket_path: Path, *, timeout_seconds: float) -> None:
    """Require a healthy `service_health` response over the configured UDS."""

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout_seconds)
            client.connect(str(socket_path))
            client.sendall(_HEALTH_REQUEST)
            with client.makefile("rb") as response_file:
                raw_response = response_file.readline()
    except OSError as error:
        raise HealthcheckError("Socket app is unavailable.") from error

    try:
        response: Any = json.loads(raw_response)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HealthcheckError("Socket app returned invalid health JSON.") from error

    expected_result = {"status": "ok", "docker_reachable": True}
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise HealthcheckError("Socket app reported an unhealthy response.")
    if response.get("result") != expected_result:
        raise HealthcheckError("Docker daemon is not reachable through the socket app.")


def main() -> None:
    """Exit successfully only when both the UDS server and Docker daemon respond."""

    check_service_health(
        SOCKET_APP_SOCKET_PATH,
        timeout_seconds=SOCKET_APP_HEALTHCHECK_TIMEOUT_SECONDS,
    )


if __name__ == "__main__":
    main()
