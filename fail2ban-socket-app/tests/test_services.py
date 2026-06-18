from __future__ import annotations

from typing import Any

import pytest

from fail2ban_socket_app import Fail2banSocketService, ProtocolException, dispatch_request


class FakeFail2banBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_jails(self) -> dict[str, Any]:
        self.calls.append(("list_jails", {}))
        return {"jails": ["portfolio-nginx-probes"]}

    def get_jail_bans(self, *, jail_name: str) -> dict[str, Any]:
        self.calls.append(("get_jail_bans", {"jail_name": jail_name}))
        return {"jail_name": jail_name, "currently_banned": 2}

    def blocked_ips_summary(self) -> dict[str, Any]:
        self.calls.append(("blocked_ips_summary", {}))
        return {"jails": [{"jail_name": "portfolio-nginx-probes", "banned_ips": ["1.2.3.4"]}]}


def test_dispatch_request_rejects_unknown_operation() -> None:
    service = Fail2banSocketService(backend=FakeFail2banBackend())

    with pytest.raises(ProtocolException) as error:
        dispatch_request({"operation": "ban_ip", "params": {"ip": "1.2.3.4"}}, service)

    assert str(error.value) == "Unsupported fail2ban socket operation: ban_ip"


def test_dispatch_request_calls_fixed_get_jail_bans_operation() -> None:
    backend = FakeFail2banBackend()
    service = Fail2banSocketService(backend=backend)

    response = dispatch_request(
        {
            "operation": "get_jail_bans",
            "params": {"jail_name": "portfolio-nginx-probes"},
        },
        service,
    )

    assert response == {
        "ok": True,
        "result": {"jail_name": "portfolio-nginx-probes", "currently_banned": 2},
    }
    assert backend.calls == [("get_jail_bans", {"jail_name": "portfolio-nginx-probes"})]


def test_dispatch_request_rejects_params_for_list_jails() -> None:
    service = Fail2banSocketService(backend=FakeFail2banBackend())

    with pytest.raises(ProtocolException) as error:
        dispatch_request({"operation": "list_jails", "params": {"jail_name": "x"}}, service)

    assert str(error.value) == "This operation does not accept parameters."
