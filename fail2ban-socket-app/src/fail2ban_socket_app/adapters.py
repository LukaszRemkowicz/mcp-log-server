"""fail2ban-client adapter for the fail2ban socket app."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from typing import Any

from .schemas import Fail2banBackend

FAIL2BAN_CLIENT_COMMAND = "fail2ban-client"
FAIL2BAN_COMMAND_TIMEOUT_SECONDS = 5
_JAIL_LIST_PATTERN = re.compile(r"Jail list:\s*(?P<jails>.+)$")
_JAIL_COUNT_PATTERN = re.compile(r"Number of jail:\s*(?P<count>\d+)$")
_CURRENTLY_BANNED_PATTERN = re.compile(r"Currently banned:\s*(?P<count>\d+)$")
_BANNED_IP_LIST_PATTERN = re.compile(r"Banned IP list:\s*(?P<ips>.*)$")

CommandRunner = Callable[[list[str], int], str]


class Fail2banBackendError(RuntimeError):
    """Expected fail2ban-client operation failure."""


class Fail2banClientAdapter(Fail2banBackend):
    """Run fixed read-only fail2ban-client diagnostics.

    This adapter is the only layer that calls `fail2ban-client`. It never
    accepts arbitrary command arguments from the socket request. Public methods
    map to fixed read-only operations and return JSON-serializable dictionaries.
    """

    def __init__(
        self,
        *,
        socket_path: str,
        timeout_seconds: int = FAIL2BAN_COMMAND_TIMEOUT_SECONDS,
        runner: CommandRunner | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds
        self.runner = runner or self._run_command

    def list_jails(self) -> dict[str, Any]:
        """Return known fail2ban jails and jail count.

        This runs the global `fail2ban-client status` command. It answers
        "which jails exist?" and does not inspect individual banned IP lists.
        """

        output = self.runner(self._command("status"), self.timeout_seconds)
        jails = self._parse_jail_list(output)
        return {"jails": jails, "jail_count": self._parse_jail_count(output, len(jails))}

    def get_jail_bans(self, *, jail_name: str) -> dict[str, Any]:
        """Return banned IP information for one fail2ban jail.

        This runs `fail2ban-client status <jail>`. It answers "who is banned
        by this jail?" and deliberately returns only the bounded read-only
        fields the MCP side will need.
        """

        output = self.runner(self._command("status", jail_name), self.timeout_seconds)
        return {
            "jail_name": jail_name,
            "currently_banned": self._parse_currently_banned(output),
            "banned_ips": self._parse_banned_ips(output),
        }

    def blocked_ips_summary(self) -> dict[str, Any]:
        """Return banned IPs grouped by jail."""

        jails = self.list_jails()["jails"]
        rows = []
        for jail_name in jails:
            if not isinstance(jail_name, str):
                continue
            status = self.get_jail_bans(jail_name=jail_name)
            rows.append(
                {
                    "jail_name": jail_name,
                    "currently_banned": status["currently_banned"],
                    "banned_ips": status["banned_ips"],
                }
            )
        return {"jails": rows}

    def _command(self, *args: str) -> list[str]:
        return [FAIL2BAN_CLIENT_COMMAND, "-s", self.socket_path, *args]

    @staticmethod
    def _run_command(command: list[str], timeout_seconds: int) -> str:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise Fail2banBackendError("Timed out waiting for fail2ban-client.") from error
        except OSError as error:
            raise Fail2banBackendError("fail2ban-client is not available.") from error
        output = f"{completed.stdout}\n{completed.stderr}".strip()
        if completed.returncode != 0:
            raise Fail2banBackendError(output or "fail2ban-client operation failed.")
        return completed.stdout

    @staticmethod
    def _parse_jail_list(output: str) -> list[str]:
        for line in output.splitlines():
            match = _JAIL_LIST_PATTERN.search(line)
            if match is None:
                continue
            return [item.strip() for item in match.group("jails").split(",") if item.strip()]
        return []

    @staticmethod
    def _parse_jail_count(output: str, fallback: int) -> int:
        for line in output.splitlines():
            match = _JAIL_COUNT_PATTERN.search(line)
            if match is not None:
                return int(match.group("count"))
        return fallback

    @staticmethod
    def _parse_currently_banned(output: str) -> int:
        for line in output.splitlines():
            match = _CURRENTLY_BANNED_PATTERN.search(line)
            if match is not None:
                return int(match.group("count"))
        return 0

    @staticmethod
    def _parse_banned_ips(output: str) -> list[str]:
        for line in output.splitlines():
            match = _BANNED_IP_LIST_PATTERN.search(line)
            if match is None:
                continue
            return [item.strip() for item in match.group("ips").split() if item.strip()]
        return []
