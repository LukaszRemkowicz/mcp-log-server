from __future__ import annotations

from fail2ban_socket_app.adapters import Fail2banClientAdapter


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], timeout: int) -> str:
        self.calls.append(command)
        assert timeout == 5
        if command[-1] == "status" and len(command) == 4:
            return "Status\n|- Number of jail:\t1\n`- Jail list:\tportfolio-nginx-probes\n"
        if command[-2:] == ["status", "portfolio-nginx-probes"]:
            return (
                "Status for the jail: portfolio-nginx-probes\n"
                "|- Filter\n"
                "|  |- Currently failed:\t0\n"
                "`- Actions\n"
                "   |- Currently banned:\t2\n"
                "   `- Banned IP list:\t1.2.3.4 5.6.7.8\n"
            )
        raise AssertionError(f"Unexpected command: {command}")


def test_list_jails_parses_fail2ban_client_output() -> None:
    runner = FakeRunner()
    adapter = Fail2banClientAdapter(socket_path="/tmp/fail2ban.sock", runner=runner)

    result = adapter.list_jails()

    assert result == {"jails": ["portfolio-nginx-probes"], "jail_count": 1}
    assert runner.calls == [["fail2ban-client", "-s", "/tmp/fail2ban.sock", "status"]]


def test_blocked_ips_summary_parses_each_jail_ban_list() -> None:
    runner = FakeRunner()
    adapter = Fail2banClientAdapter(socket_path="/tmp/fail2ban.sock", runner=runner)

    result = adapter.blocked_ips_summary()

    assert result == {
        "jails": [
            {
                "jail_name": "portfolio-nginx-probes",
                "currently_banned": 2,
                "banned_ips": ["1.2.3.4", "5.6.7.8"],
            }
        ]
    }
