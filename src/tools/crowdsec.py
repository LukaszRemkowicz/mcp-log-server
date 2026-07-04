"""MCP tool for strictly allowlisted CrowdSec live-status diagnostics."""

from __future__ import annotations

import logging
from typing import cast

from fastmcp.server.dependencies import get_http_request
from fastmcp.tools.base import ToolResult

from auth.mcp_authorized_manifests import AuthorizedProjectManifests
from auth.scopes import MCP_STATUS_READ_SCOPE
from decorators import project_authorized_tool, workflow_discoverable_tool
from logging_config import get_logger
from manifests.models import Manifest, SourceDefinition
from services.crowdsec_service import CrowdSecActivity, CrowdSecService
from tools.agent_hints import INSPECT_LIVE_CROWDSEC_ACTIVITY_TOOL_DESCRIPTION
from tools.models import CrowdSecSectionPayload, InspectLiveCrowdSecActivityPayload
from utils.mcp_errors import build_agent_tool_error_result

logger: logging.Logger = get_logger("tools.crowdsec")
crowdsec_service = CrowdSecService()


def _get_authorized_manifest(project_name: str) -> Manifest | None:
    """Return one request-state manifest prepared by AuthorizedManifestsMiddleware."""

    request = get_http_request()
    authorized_manifests = cast(
        AuthorizedProjectManifests,
        request.state.authorized_manifests,
    )
    return authorized_manifests.manifests.get(project_name)


def _looks_like_crowdsec_source(source: SourceDefinition) -> bool:
    if source.source_type != "docker":
        return False
    values = [
        source.source_key,
        source.target,
        source.compose_project or "",
        source.compose_service or "",
    ]
    return any("crowdsec" in value.lower() for value in values)


def _resolve_crowdsec_source(
    *,
    manifest: Manifest,
    source_key: str | None,
) -> SourceDefinition | None:
    if source_key is not None:
        return next(
            (
                source
                for source in manifest.sources
                if source.source_key == source_key and source.source_type == "docker"
            ),
            None,
        )
    return next(
        (_source for _source in manifest.sources if _looks_like_crowdsec_source(_source)),
        None,
    )


def _build_crowdsec_payload(
    *,
    project_name: str,
    activity: CrowdSecActivity,
) -> InspectLiveCrowdSecActivityPayload:
    """Convert service-layer CrowdSec status into the MCP response contract."""

    return InspectLiveCrowdSecActivityPayload(
        action="inspect_live_crowdsec_activity",
        project_name=project_name,
        inspection_status=activity.inspection_status,
        container_name=activity.container_name,
        error_code=activity.error_code,
        message=activity.message,
        retry_tips=activity.retry_tips,
        sections=[
            CrowdSecSectionPayload(
                name=section.name,
                inspection_status=section.inspection_status,
                command=section.command,
                exit_code=section.exit_code,
                output=section.output,
                truncated=section.truncated,
                error=section.error,
            )
            for section in activity.sections
        ],
    )


@workflow_discoverable_tool(
    MCP_STATUS_READ_SCOPE,
    mcp_description=INSPECT_LIVE_CROWDSEC_ACTIVITY_TOOL_DESCRIPTION,
)
@project_authorized_tool
async def inspect_live_crowdsec_activity(
    project_name: str | None = None,
    source_key: str | None = None,
) -> ToolResult:
    """Inspect live CrowdSec runtime state through fixed Docker gateway reads."""

    assert project_name is not None
    manifest = _get_authorized_manifest(project_name)
    if manifest is None:
        return build_agent_tool_error_result(
            error_code="unknown_project",
            message=f"Unknown project {project_name!r}. No persisted manifest was found.",
            retry_tips=["Call list_projects to discover available project names."],
            details={"project_name": project_name},
        )

    source = _resolve_crowdsec_source(manifest=manifest, source_key=source_key)
    if source is None:
        return build_agent_tool_error_result(
            error_code="crowdsec_source_not_found",
            message="No CrowdSec docker source was found in the authorized project manifest.",
            retry_tips=[
                "Call read_project_manifest for this project and check docker source keys.",
                "Add a CrowdSec docker source such as crowdsec_runtime to the project manifest.",
            ],
            details={
                "project_name": project_name,
                "source_key": source_key,
                "available_source_keys": [source.source_key for source in manifest.sources],
            },
        )

    activity = crowdsec_service.inspect_activity(container_name=source.target)
    payload = _build_crowdsec_payload(project_name=project_name, activity=activity)
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "inspect_live_crowdsec_activity",
            "project_name": payload.project_name,
            "inspection_status": payload.inspection_status,
            "section_count": len(payload.sections),
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))
