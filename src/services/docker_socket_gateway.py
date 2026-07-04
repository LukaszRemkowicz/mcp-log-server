"""Shared Unix-socket client for fixed Docker-backed socket operations."""

from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from conf import settings
from exceptions import DockerSocketGatewayError


class DockerSocketGatewayClient:
    """Call fixed Docker-backed operations through the local Unix socket app."""

    def __init__(
        self,
        *,
        socket_path: Path | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.socket_path = socket_path or settings.SOCKET_APP_SOCKET_PATH
        self.timeout_seconds = timeout_seconds or settings.DOCKER_SOCKET_APP_TIMEOUT_SECONDS

    def request(self, operation: str, params: Mapping[str, object]) -> dict[str, Any]:
        """Return one JSON result from the Docker-backed socket app."""

        request = (
            json.dumps(
                {"operation": operation, "params": params},
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
            + b"\n"
        )
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(self.timeout_seconds)
                client.connect(str(self.socket_path))
                client.sendall(request)
                response = self._read_response(client)
        except TimeoutError as error:
            raise DockerSocketGatewayError(
                message="Timed out waiting for socket app.",
                error_code="socket_app_timeout",
            ) from error
        except OSError as error:
            raise DockerSocketGatewayError(
                message="Socket app is not available in the current runtime.",
                error_code="socket_app_unavailable",
            ) from error

        try:
            decoded = json.loads(response.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise DockerSocketGatewayError(message="Socket app returned invalid JSON.") from error
        if not isinstance(decoded, dict):
            raise DockerSocketGatewayError(message="Socket app returned an invalid response.")
        if decoded.get("ok") is True and isinstance(decoded.get("result"), dict):
            return decoded["result"]
        error_payload = decoded.get("error")
        message = (
            str(error_payload.get("message"))
            if isinstance(error_payload, dict) and error_payload.get("message")
            else "Socket app operation failed."
        )
        raise DockerSocketGatewayError(message=message)

    @staticmethod
    def _read_response(client: socket.socket) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        return b"".join(chunks).split(b"\n", 1)[0]
