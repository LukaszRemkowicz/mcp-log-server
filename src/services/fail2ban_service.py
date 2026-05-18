"""Strictly allowlisted fail2ban live-status diagnostics."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from urllib.request import Request, urlopen

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
class Fail2banCommandResult:
    """Small command result shape shared by subprocess and proxy responses."""

    returncode: int
    stdout: str
    stderr: str


class Fail2banService:
    """Run and parse fixed fail2ban-client status commands."""

    def __init__(
        self,
        *,
        socket_path: str | Path | None = None,
        client_command: str | None = None,
        proxy_url: str | None = None,
        jails: list[str] | None = None,
        command_timeout_seconds: int | None = None,
    ) -> None:
        """Create a service that talks to the configured fail2ban socket."""

        self.socket_path = Path(socket_path or settings.FAIL2BAN_SOCKET_PATH)
        self.client_command = client_command or settings.FAIL2BAN_CLIENT_COMMAND
        self.proxy_url = proxy_url if proxy_url is not None else settings.FAIL2BAN_PROXY_URL
        self.jails = jails or settings.FAIL2BAN_JAILS
        self.command_timeout_seconds = (
            command_timeout_seconds or settings.FAIL2BAN_COMMAND_TIMEOUT_SECONDS
        )

    def inspect_activity(self) -> Fail2banActivity:
        """Return live fail2ban service and jail status using allowlisted commands."""

        service_result = self._run_status_command(self._build_status_command())
        if service_result.inspection_status == "unavailable":
            return Fail2banActivity(
                inspection_status="unavailable",
                service=service_result,
                jails=[],
                error_code="fail2ban_client_unavailable",
                message="fail2ban-client is not available to the MCP runtime.",
                retry_tips=[
                    "Install fail2ban-client in the MCP image and mount the host fail2ban socket.",
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
                    "Check that the host fail2ban socket is mounted into the MCP container.",
                    "Check fail2ban socket permissions and jail names.",
                ]
            ),
        )

    def _build_status_command(self) -> list[str]:
        """Return the allowlisted service-status command for the configured socket."""

        return [self.client_command, "-s", str(self.socket_path), "status"]

    def _run_status_command(self, command: list[str]) -> Fail2banServiceStatus:
        """Run one fixed service-status command."""

        try:
            completed = self._run_command(command)
        except FileNotFoundError:
            return Fail2banServiceStatus(
                inspection_status="unavailable",
                error="fail2ban-client executable was not found.",
            )
        except (subprocess.SubprocessError, OSError) as error:
            return Fail2banServiceStatus(
                inspection_status="error",
                error=str(error),
            )

        if completed.returncode != 0:
            return Fail2banServiceStatus(
                inspection_status="error",
                raw_output=completed.stdout,
                error=completed.stderr.strip() or f"Command exited with {completed.returncode}.",
            )
        return self._parse_service_status(completed.stdout)

    def _run_jail_command(self, jail: str) -> Fail2banJailStatus:
        """Run one fixed jail-status command."""

        try:
            completed = self._run_command([*self._build_status_command(), jail])
        except FileNotFoundError:
            return Fail2banJailStatus(
                jail=jail,
                inspection_status="unavailable",
                error="fail2ban-client executable was not found.",
            )
        except (subprocess.SubprocessError, OSError) as error:
            return Fail2banJailStatus(
                jail=jail,
                inspection_status="error",
                error=str(error),
            )

        if completed.returncode != 0:
            return Fail2banJailStatus(
                jail=jail,
                inspection_status="error",
                raw_output=completed.stdout,
                error=completed.stderr.strip() or f"Command exited with {completed.returncode}.",
            )
        return self._parse_jail_status(jail=jail, output=completed.stdout)

    def _run_command(
        self,
        command: list[str],
    ) -> subprocess.CompletedProcess[str] | Fail2banCommandResult:
        """Run one allowlisted command without shell expansion."""

        if self.proxy_url:
            return self._run_proxy_command(command)

        return subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=self.command_timeout_seconds,
        )

    def _run_proxy_command(self, command: list[str]) -> Fail2banCommandResult:
        """Run one fixed fail2ban status command through the root proxy service."""

        status_command = self._build_status_command()
        if command == status_command:
            endpoint = "/status"
        elif (
            len(command) == len(status_command) + 1
            and command[: len(status_command)] == status_command
        ):
            jail = command[-1]
            if jail not in self.jails:
                raise ValueError(f"Fail2ban jail is not allowlisted: {jail}")
            endpoint = f"/status/{quote(jail, safe='')}"
        else:
            raise ValueError("Fail2ban proxy rejected a non-allowlisted command shape.")

        request = Request(
            f"{self.proxy_url}{endpoint}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urlopen(request, timeout=self.command_timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        return Fail2banCommandResult(
            returncode=int(payload.get("returncode", 1)),
            stdout=str(payload.get("stdout", "")),
            stderr=str(payload.get("stderr", "")),
        )

    @staticmethod
    def _parse_service_status(output: str) -> Fail2banServiceStatus:
        """Parse the global fail2ban service status output."""

        values = _parse_fail2ban_key_values(output)
        jails = _parse_csv_or_space_list(values.get("jail list"))
        return Fail2banServiceStatus(
            inspection_status="ok",
            jail_count=_parse_int(values.get("number of jail")),
            jails=jails,
            raw_output=output,
        )

    @staticmethod
    def _parse_jail_status(*, jail: str, output: str) -> Fail2banJailStatus:
        """Parse one allowlisted fail2ban jail status output."""

        values = _parse_fail2ban_key_values(output)
        return Fail2banJailStatus(
            jail=jail,
            inspection_status="ok",
            currently_failed=_parse_int(values.get("currently failed")),
            total_failed=_parse_int(values.get("total failed")),
            currently_banned=_parse_int(values.get("currently banned")),
            total_banned=_parse_int(values.get("total banned")),
            banned_ips=_parse_csv_or_space_list(values.get("banned ip list")),
            raw_output=output,
        )


def _parse_fail2ban_key_values(output: str) -> dict[str, str]:
    """Extract loose key/value lines from fail2ban-client output."""

    values: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip(" |`-")
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip().lower()] = value.strip()
    return values


def _parse_int(value: str | None) -> int | None:
    """Parse an optional integer status value."""

    if value is None or not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_csv_or_space_list(value: str | None) -> list[str]:
    """Parse fail2ban lists that may be comma-separated or whitespace-separated."""

    if value is None:
        return []
    normalized = value.replace(",", " ")
    return [item for item in normalized.split() if item]
