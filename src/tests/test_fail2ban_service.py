from __future__ import annotations

import json
import subprocess
from io import BytesIO

from pytest_mock import MockerFixture

from services.fail2ban_service import Fail2banService

FAIL2BAN_SOCKET_PATH = "/var/run/fail2ban/fail2ban.sock"
FAIL2BAN_JAILS = ["portfolio-nginx-probes", "portfolio-traefik-probes"]


def test_fail2ban_service_runs_only_allowlisted_status_commands(
    mocker: MockerFixture,
) -> None:
    """Verify live fail2ban diagnostics never accept caller-provided commands."""

    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=(
            "Status\n"
            "|- Number of jail:\t2\n"
            "`- Jail list:\tportfolio-nginx-probes, portfolio-traefik-probes\n"
        ),
        stderr="",
    )
    run = mocker.patch("services.fail2ban_service.subprocess.run", return_value=completed)

    result = Fail2banService(
        socket_path=FAIL2BAN_SOCKET_PATH,
        jails=FAIL2BAN_JAILS,
    ).inspect_activity()

    expected_commands = [
        ["fail2ban-client", "-s", FAIL2BAN_SOCKET_PATH, "status"],
        ["fail2ban-client", "-s", FAIL2BAN_SOCKET_PATH, "status", FAIL2BAN_JAILS[0]],
        ["fail2ban-client", "-s", FAIL2BAN_SOCKET_PATH, "status", FAIL2BAN_JAILS[1]],
    ]
    assert [call.args[0] for call in run.call_args_list] == expected_commands
    assert result.inspection_status == "ok"
    assert result.service.jail_count == 2
    assert result.service.jails == ["portfolio-nginx-probes", "portfolio-traefik-probes"]


def test_fail2ban_service_parses_banned_ips_from_jail_status(
    mocker: MockerFixture,
) -> None:
    """Verify jail status output becomes structured banned-IP metadata."""

    service_status = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=(
            "Status\n"
            "|- Number of jail:\t2\n"
            "`- Jail list:\tportfolio-nginx-probes, portfolio-traefik-probes\n"
        ),
        stderr="",
    )
    nginx_status = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=(
            "Status for the jail: portfolio-nginx-probes\n"
            "|- Filter\n"
            "|  |- Currently failed:\t4\n"
            "|  `- Total failed:\t11\n"
            "`- Actions\n"
            "   |- Currently banned:\t2\n"
            "   |- Total banned:\t3\n"
            "   `- Banned IP list:\t203.0.113.10 198.51.100.2\n"
        ),
        stderr="",
    )
    traefik_status = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=(
            "Status for the jail: portfolio-traefik-probes\n"
            "|- Filter\n"
            "|  |- Currently failed:\t0\n"
            "|  `- Total failed:\t5\n"
            "`- Actions\n"
            "   |- Currently banned:\t0\n"
            "   |- Total banned:\t1\n"
            "   `- Banned IP list:\t\n"
        ),
        stderr="",
    )
    mocker.patch(
        "services.fail2ban_service.subprocess.run",
        side_effect=[service_status, nginx_status, traefik_status],
    )

    result = Fail2banService(
        socket_path=FAIL2BAN_SOCKET_PATH,
        jails=FAIL2BAN_JAILS,
    ).inspect_activity()

    nginx = result.jails[0]
    assert nginx.jail == "portfolio-nginx-probes"
    assert nginx.inspection_status == "ok"
    assert nginx.currently_failed == 4
    assert nginx.total_failed == 11
    assert nginx.currently_banned == 2
    assert nginx.total_banned == 3
    assert nginx.banned_ips == ["203.0.113.10", "198.51.100.2"]

    traefik = result.jails[1]
    assert traefik.banned_ips == []


def test_fail2ban_service_returns_unavailable_when_client_is_missing(
    mocker: MockerFixture,
) -> None:
    """Verify missing fail2ban-client becomes agent-facing unavailable state."""

    mocker.patch(
        "services.fail2ban_service.subprocess.run",
        side_effect=FileNotFoundError("fail2ban-client"),
    )

    result = Fail2banService(
        socket_path=FAIL2BAN_SOCKET_PATH,
        jails=FAIL2BAN_JAILS,
    ).inspect_activity()

    assert result.inspection_status == "unavailable"
    assert result.error_code == "fail2ban_client_unavailable"
    assert result.jails == []
    assert result.retry_tips


def test_fail2ban_service_uses_proxy_for_allowlisted_status_commands(
    mocker: MockerFixture,
) -> None:
    """Verify proxy mode uses fixed HTTP endpoints instead of subprocess calls."""

    class FakeResponse(BytesIO):
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    payloads = [
        {
            "returncode": 0,
            "stdout": (
                "Status\n"
                "|- Number of jail:\t2\n"
                "`- Jail list:\tportfolio-nginx-probes, portfolio-traefik-probes\n"
            ),
            "stderr": "",
        },
        {
            "returncode": 0,
            "stdout": (
                "Status for the jail: portfolio-nginx-probes\n"
                "|- Filter\n"
                "|  |- Currently failed:\t1\n"
                "`- Actions\n"
                "   |- Currently banned:\t0\n"
                "   `- Banned IP list:\t\n"
            ),
            "stderr": "",
        },
        {
            "returncode": 0,
            "stdout": (
                "Status for the jail: portfolio-traefik-probes\n"
                "|- Filter\n"
                "|  |- Currently failed:\t0\n"
                "`- Actions\n"
                "   |- Currently banned:\t0\n"
                "   `- Banned IP list:\t\n"
            ),
            "stderr": "",
        },
    ]
    urlopen = mocker.patch(
        "services.fail2ban_service.urlopen",
        side_effect=[FakeResponse(json.dumps(payload).encode("utf-8")) for payload in payloads],
    )
    run = mocker.patch("services.fail2ban_service.subprocess.run")

    result = Fail2banService(
        socket_path=FAIL2BAN_SOCKET_PATH,
        proxy_url="http://fail2ban-proxy:8765",
        jails=FAIL2BAN_JAILS,
    ).inspect_activity()

    assert run.call_count == 0
    assert [call.args[0].full_url for call in urlopen.call_args_list] == [
        "http://fail2ban-proxy:8765/status",
        "http://fail2ban-proxy:8765/status/portfolio-nginx-probes",
        "http://fail2ban-proxy:8765/status/portfolio-traefik-probes",
    ]
    assert result.inspection_status == "ok"
    assert result.service.jail_count == 2
