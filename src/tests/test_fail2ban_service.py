from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.fail2ban_service import Fail2banService, Fail2banSocketAppError

FAIL2BAN_JAILS = ["portfolio-nginx-probes", "portfolio-traefik-probes"]


class FakeFail2banSocketClient:
    def __init__(
        self,
        responses: dict[str, dict[str, Any]] | None = None,
        error: Fail2banSocketAppError | None = None,
    ) -> None:
        self.responses = responses or {}
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    def request(self, operation: str, params: Mapping[str, object]) -> dict[str, Any]:
        self.calls.append((operation, dict(params)))
        if self.error is not None:
            raise self.error
        if operation == "get_jail_bans":
            jail_name = str(params["jail_name"])
            return self.responses[jail_name]
        return self.responses[operation]


def test_fail2ban_service_calls_only_fixed_socket_operations() -> None:
    """Verify live fail2ban diagnostics never accept caller-provided commands."""

    client = FakeFail2banSocketClient(
        {
            "list_jails": {
                "jail_count": 2,
                "jails": ["portfolio-nginx-probes", "portfolio-traefik-probes"],
            },
            "portfolio-nginx-probes": {"currently_banned": 0, "banned_ips": []},
            "portfolio-traefik-probes": {"currently_banned": 0, "banned_ips": []},
        }
    )

    result = Fail2banService(
        socket_client=client,
        jails=FAIL2BAN_JAILS,
    ).inspect_activity()

    assert client.calls == [
        ("list_jails", {}),
        ("get_jail_bans", {"jail_name": "portfolio-nginx-probes"}),
        ("get_jail_bans", {"jail_name": "portfolio-traefik-probes"}),
    ]
    assert result.inspection_status == "ok"
    assert result.service.jail_count == 2
    assert result.service.jails == ["portfolio-nginx-probes", "portfolio-traefik-probes"]


def test_fail2ban_service_maps_banned_ips_from_socket_app() -> None:
    """Verify socket-app jail payloads become structured banned-IP metadata."""

    client = FakeFail2banSocketClient(
        {
            "list_jails": {
                "jail_count": 2,
                "jails": ["portfolio-nginx-probes", "portfolio-traefik-probes"],
            },
            "portfolio-nginx-probes": {
                "currently_banned": 2,
                "banned_ips": ["203.0.113.10", "198.51.100.2"],
            },
            "portfolio-traefik-probes": {"currently_banned": 0, "banned_ips": []},
        }
    )

    result = Fail2banService(
        socket_client=client,
        jails=FAIL2BAN_JAILS,
    ).inspect_activity()

    nginx = result.jails[0]
    assert nginx.jail == "portfolio-nginx-probes"
    assert nginx.inspection_status == "ok"
    assert nginx.currently_failed is None
    assert nginx.total_failed is None
    assert nginx.currently_banned == 2
    assert nginx.total_banned is None
    assert nginx.banned_ips == ["203.0.113.10", "198.51.100.2"]

    traefik = result.jails[1]
    assert traefik.banned_ips == []


def test_fail2ban_service_returns_unavailable_when_socket_app_is_missing() -> None:
    """Verify a missing socket app becomes agent-facing unavailable state."""

    client = FakeFail2banSocketClient(
        error=Fail2banSocketAppError(
            message="Fail2ban socket app is not available in the current runtime.",
            error_code="fail2ban_socket_app_unavailable",
        )
    )

    result = Fail2banService(
        socket_client=client,
        jails=FAIL2BAN_JAILS,
    ).inspect_activity()

    assert client.calls == [("list_jails", {})]
    assert result.inspection_status == "unavailable"
    assert result.error_code == "fail2ban_socket_app_unavailable"
    assert result.jails == []
    assert result.retry_tips


def test_fail2ban_service_marks_jail_error_when_socket_operation_fails() -> None:
    """Verify per-jail socket failures keep the service payload structured."""

    class PartiallyFailingClient(FakeFail2banSocketClient):
        def request(self, operation: str, params: Mapping[str, object]) -> dict[str, Any]:
            self.calls.append((operation, dict(params)))
            if operation == "get_jail_bans":
                raise Fail2banSocketAppError(message="fail2ban-client operation failed.")
            return self.responses[operation]

    client = PartiallyFailingClient(
        {
            "list_jails": {
                "jail_count": 1,
                "jails": ["portfolio-nginx-probes"],
            }
        }
    )

    result = Fail2banService(
        socket_client=client,
        jails=["portfolio-nginx-probes"],
    ).inspect_activity()

    assert result.inspection_status == "error"
    assert result.error_code == "fail2ban_status_error"
    assert result.jails[0].inspection_status == "error"
    assert result.jails[0].error == "fail2ban-client operation failed."
