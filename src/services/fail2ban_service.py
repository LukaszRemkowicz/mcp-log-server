"""Strictly allowlisted fail2ban live-status diagnostics."""

from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from conf import settings

InspectionStatus = Literal["ok", "error", "unavailable"]


@dataclass(frozen=True, slots=True)
class Fail2banServiceStatus:
    """Parsed output from `fail2ban-client status`."""

    inspection_status: InspectionStatus
    jail_count: int | None = None
    jails: list[str] = field(default_factory=list)
    raw_output: str = ""
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Fail2banJailStatus:
    """Parsed output from one allowlisted fail2ban jail status command."""

    jail: str
    inspection_status: InspectionStatus
    currently_failed: int | None = None
    total_failed: int | None = None
    currently_banned: int | None = None
    total_banned: int | None = None
    banned_ips: list[str] = field(default_factory=list)
    raw_output: str = ""
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Fail2banActivity:
    """Structured live fail2ban status returned by the service."""

    inspection_status: InspectionStatus
    service: Fail2banServiceStatus
    jails: list[Fail2banJailStatus]
    error_code: str | None = None
    message: str | None = None
    retry_tips: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Fail2banSocketAppError(Exception):
    """Expected failure returned by the fail2ban socket app boundary."""

    message: str
    error_code: str = "fail2ban_socket_app_error"


class Fail2banSocketAppClient:
    """Call fixed fail2ban operations through the local Unix socket app."""

    def __init__(
        self,
        *,
        socket_path: Path | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        """Create a client for the configured fail2ban socket app."""

        self.socket_path = socket_path or settings.FAIL2BAN_SOCKET_APP_SOCKET_PATH
        self.timeout_seconds = timeout_seconds or settings.FAIL2BAN_SOCKET_APP_TIMEOUT_SECONDS

    def request(self, operation: str, params: Mapping[str, object]) -> dict[str, Any]:
        """Return one JSON result from the fail2ban socket app."""

        request = (
            json.dumps(
                {"operation": operation, "params": params},
                separators=(",", ":"),
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
            raise Fail2banSocketAppError(
                message="Timed out waiting for fail2ban socket app.",
                error_code="fail2ban_socket_app_timeout",
            ) from error
        except OSError as error:
            raise Fail2banSocketAppError(
                message="Fail2ban socket app is not available in the current runtime.",
                error_code="fail2ban_socket_app_unavailable",
            ) from error

        try:
            decoded = json.loads(response.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise Fail2banSocketAppError(
                message="Fail2ban socket app returned invalid JSON."
            ) from error
        if not isinstance(decoded, dict):
            raise Fail2banSocketAppError(
                message="Fail2ban socket app returned an invalid response."
            )
        if decoded.get("ok") is True and isinstance(decoded.get("result"), dict):
            return decoded["result"]
        error_payload = decoded.get("error")
        message = (
            str(error_payload.get("message"))
            if isinstance(error_payload, dict) and error_payload.get("message")
            else "Fail2ban socket app operation failed."
        )
        raise Fail2banSocketAppError(message=message)

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


class Fail2banSocketClientProtocol(Protocol):
    """Minimal client contract used by `Fail2banService`."""

    def request(self, operation: str, params: Mapping[str, object]) -> dict[str, Any]:
        """Return one result from the fail2ban socket app."""


class Fail2banService:
    """Inspect fail2ban activity through the fixed Unix-socket app."""

    def __init__(
        self,
        *,
        socket_client: Fail2banSocketClientProtocol | None = None,
        jails: list[str] | None = None,
    ) -> None:
        """Create a service that talks to the fail2ban socket app."""

        self.socket_client = socket_client or Fail2banSocketAppClient()
        self.jails = jails or settings.FAIL2BAN_JAILS

    def inspect_activity(self) -> Fail2banActivity:
        """Return live fail2ban service and jail status using fixed socket operations."""

        service_result = self._list_jails()
        if service_result.inspection_status == "unavailable":
            return Fail2banActivity(
                inspection_status="unavailable",
                service=service_result,
                jails=[],
                error_code="fail2ban_socket_app_unavailable",
                message="Fail2ban socket app is not available to the MCP runtime.",
                retry_tips=[
                    "Start the fail2ban socket app container.",
                    "Check that MCP can access the fail2ban socket app Unix socket.",
                    "Use collected fail2ban log sources if live status is not available.",
                ],
            )

        jail_results = [self._run_jail_command(jail) for jail in self.jails]
        status: InspectionStatus = (
            "ok"
            if service_result.inspection_status == "ok"
            and all(item.inspection_status == "ok" for item in jail_results)
            else "error"
        )
        return Fail2banActivity(
            inspection_status=status,
            service=service_result,
            jails=jail_results,
            error_code=None if status == "ok" else "fail2ban_status_error",
            message=None if status == "ok" else "One or more fail2ban status commands failed.",
            retry_tips=(
                []
                if status == "ok"
                else [
                    "Check that the host fail2ban socket is mounted into the socket app.",
                    "Check fail2ban socket app permissions and jail names.",
                ]
            ),
        )

    def _list_jails(self) -> Fail2banServiceStatus:
        """Return the configured fail2ban service status from the socket app."""
        try:
            payload = self.socket_client.request("list_jails", {})
        except Fail2banSocketAppError as error:
            if error.error_code == "fail2ban_socket_app_unavailable":
                return Fail2banServiceStatus(
                    inspection_status="unavailable",
                    error=error.message,
                )
            return Fail2banServiceStatus(
                inspection_status="error",
                error=error.message,
            )
        return Fail2banServiceStatus(
            inspection_status="ok",
            jail_count=_payload_int(payload.get("jail_count")),
            jails=_payload_str_list(payload.get("jails")),
        )

    def _run_jail_command(self, jail: str) -> Fail2banJailStatus:
        """Return one configured fail2ban jail status from the socket app."""

        try:
            payload = self.socket_client.request("get_jail_bans", {"jail_name": jail})
        except Fail2banSocketAppError as error:
            return Fail2banJailStatus(
                jail=jail,
                inspection_status="error",
                error=error.message,
            )
        return Fail2banJailStatus(
            jail=jail,
            inspection_status="ok",
            currently_banned=_payload_int(payload.get("currently_banned")),
            banned_ips=_payload_str_list(payload.get("banned_ips")),
        )


def _payload_int(value: object) -> int | None:
    """Return an integer field from a socket-app payload."""

    if not isinstance(value, int):
        return None
    return value


def _payload_str_list(value: object) -> list[str]:
    """Return a string list field from a socket-app payload."""

    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
