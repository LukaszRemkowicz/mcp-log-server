"""Strictly allowlisted CrowdSec live-status diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from exceptions import DockerSocketGatewayError
from services.docker_socket_gateway import DockerSocketGatewayClient

InspectionStatus = Literal["ok", "error", "unavailable"]


@dataclass(frozen=True, slots=True)
class CrowdSecSection:
    """One fixed CrowdSec diagnostic command result."""

    name: str
    inspection_status: InspectionStatus
    command: list[str] = field(default_factory=list)
    exit_code: int | None = None
    output: str = ""
    truncated: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CrowdSecActivity:
    """Structured live CrowdSec status returned by the service."""

    inspection_status: InspectionStatus
    container_name: str
    sections: list[CrowdSecSection]
    error_code: str | None = None
    message: str | None = None
    retry_tips: list[str] = field(default_factory=list)


class CrowdSecGatewayClientProtocol(Protocol):
    """Minimal client contract used by `CrowdSecService`."""

    def request(self, operation: str, params: Mapping[str, object]) -> dict[str, Any]:
        """Return one result from the generic socket app."""


class CrowdSecService:
    """Inspect CrowdSec activity through the fixed Docker socket gateway."""

    def __init__(
        self,
        *,
        gateway_client: CrowdSecGatewayClientProtocol | None = None,
    ) -> None:
        """Create a service that asks the generic socket app for CrowdSec diagnostics."""

        self.gateway_client = gateway_client or DockerSocketGatewayClient()

    def inspect_activity(self, *, container_name: str) -> CrowdSecActivity:
        """Return live CrowdSec diagnostics using fixed `cscli` reads."""

        try:
            payload = self.gateway_client.request(
                "crowdsec_activity",
                {"container_name": container_name},
            )
        except DockerSocketGatewayError as error:
            inspection_status: InspectionStatus = (
                "unavailable" if error.error_code == "socket_app_unavailable" else "error"
            )
            return CrowdSecActivity(
                inspection_status=inspection_status,
                container_name=container_name,
                sections=[],
                error_code=error.error_code,
                message=error.message,
                retry_tips=[
                    "Check that the generic socket app container is running.",
                    "Check that MCP can access the generic socket app Unix socket.",
                    "Check that the CrowdSec container is running and named as configured.",
                ],
            )

        sections = self._sections_from_payload(payload)
        status: InspectionStatus = (
            "ok"
            if sections and all(section.inspection_status == "ok" for section in sections)
            else "error"
        )
        return CrowdSecActivity(
            inspection_status=status,
            container_name=str(payload.get("container_name") or container_name),
            sections=sections,
            error_code=None if status == "ok" else "crowdsec_status_error",
            message=None if status == "ok" else "One or more CrowdSec diagnostic commands failed.",
            retry_tips=(
                []
                if status == "ok"
                else [
                    "Check `docker logs crowdsec` for CrowdSec runtime errors.",
                    "Check Traefik bouncer authentication if output is missing.",
                    "Check CrowdSec AppSec configuration if appsec metrics are unavailable.",
                ]
            ),
        )

    @classmethod
    def _sections_from_payload(cls, payload: Mapping[str, Any]) -> list[CrowdSecSection]:
        raw_sections = payload.get("sections")
        if not isinstance(raw_sections, dict):
            return []
        sections: list[CrowdSecSection] = []
        for name in ["decisions", "appsec_metrics", "bouncers", "alerts", "collections"]:
            raw_section = raw_sections.get(name)
            if not isinstance(raw_section, dict):
                sections.append(
                    CrowdSecSection(
                        name=name,
                        inspection_status="error",
                        error="CrowdSec diagnostic section was missing.",
                    )
                )
                continue
            ok = raw_section.get("ok") is True
            sections.append(
                CrowdSecSection(
                    name=name,
                    inspection_status="ok" if ok else "error",
                    command=cls._payload_str_list(raw_section.get("command")),
                    exit_code=cls._payload_int(raw_section.get("exit_code")),
                    output=str(raw_section.get("output") or ""),
                    truncated=raw_section.get("truncated") is True,
                    error=(
                        None if ok else str(raw_section.get("output") or "CrowdSec command failed.")
                    ),
                )
            )
        return sections

    @staticmethod
    def _payload_int(value: object) -> int | None:
        return value if isinstance(value, int) else None

    @staticmethod
    def _payload_str_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]
