from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from exceptions import DockerSocketGatewayError
from services.crowdsec_service import CrowdSecService


class FakeCrowdSecGatewayClient:
    def __init__(
        self,
        response: dict[str, Any] | None = None,
        error: DockerSocketGatewayError | None = None,
    ) -> None:
        self.response = response or {}
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    def request(self, operation: str, params: Mapping[str, object]) -> dict[str, Any]:
        self.calls.append((operation, dict(params)))
        if self.error is not None:
            raise self.error
        return self.response


def _ok_section(command: list[str], output: str) -> dict[str, Any]:
    return {
        "command": command,
        "exit_code": 0,
        "ok": True,
        "output": output,
        "truncated": False,
    }


def test_crowdsec_service_calls_only_fixed_docker_gateway_operation() -> None:
    """Verify live CrowdSec diagnostics use one fixed Docker gateway operation."""

    client = FakeCrowdSecGatewayClient(
        {
            "container_name": "crowdsec",
            "sections": {
                "decisions": _ok_section(["cscli", "decisions", "list"], "No active decisions"),
                "appsec_metrics": _ok_section(
                    ["cscli", "metrics", "show", "appsec"],
                    "Processed 11",
                ),
                "bouncers": _ok_section(["cscli", "bouncers", "list"], "traefik valid"),
                "alerts": _ok_section(["cscli", "alerts", "list"], "No alerts"),
                "collections": _ok_section(
                    ["cscli", "collections", "list"],
                    "crowdsecurity/traefik",
                ),
            },
        }
    )

    result = CrowdSecService(
        gateway_client=client,
    ).inspect_activity(container_name="crowdsec")

    assert client.calls == [("crowdsec_activity", {"container_name": "crowdsec"})]
    assert result.inspection_status == "ok"
    assert [section.name for section in result.sections] == [
        "decisions",
        "appsec_metrics",
        "bouncers",
        "alerts",
        "collections",
    ]
    assert result.sections[0].command == ["cscli", "decisions", "list"]


def test_crowdsec_service_marks_failed_section_as_error() -> None:
    """Verify partial CrowdSec command failures keep the payload structured."""

    client = FakeCrowdSecGatewayClient(
        {
            "container_name": "crowdsec",
            "sections": {
                "decisions": {
                    "command": ["cscli", "decisions", "list"],
                    "exit_code": 1,
                    "ok": False,
                    "output": "unable to connect to LAPI",
                    "truncated": False,
                },
                "appsec_metrics": _ok_section(["cscli", "metrics", "show", "appsec"], ""),
                "bouncers": _ok_section(["cscli", "bouncers", "list"], ""),
                "alerts": _ok_section(["cscli", "alerts", "list"], ""),
                "collections": _ok_section(["cscli", "collections", "list"], ""),
            },
        }
    )

    result = CrowdSecService(gateway_client=client).inspect_activity(container_name="crowdsec")

    assert result.inspection_status == "error"
    assert result.error_code == "crowdsec_status_error"
    assert result.sections[0].inspection_status == "error"
    assert result.sections[0].error == "unable to connect to LAPI"


def test_crowdsec_service_returns_unavailable_when_docker_gateway_is_missing() -> None:
    """Verify a missing Docker gateway becomes agent-facing unavailable state."""

    client = FakeCrowdSecGatewayClient(
        error=DockerSocketGatewayError(
            message="Socket app is not available in the current runtime.",
            error_code="socket_app_unavailable",
        )
    )

    result = CrowdSecService(gateway_client=client).inspect_activity(container_name="crowdsec")

    assert client.calls == [("crowdsec_activity", {"container_name": "crowdsec"})]
    assert result.inspection_status == "unavailable"
    assert result.error_code == "socket_app_unavailable"
    assert result.sections == []
    assert result.retry_tips
