"""Landingpage Django socket MCP tools."""

from __future__ import annotations

import logging

from fastmcp.server.auth import require_scopes
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent, ToolAnnotations

from app import mcp
from auth.scopes import CONTAINER_FILES_READ_SCOPE
from decorators import project_authorized_tool
from exceptions import DockerSocketGatewayError
from logging_config import get_logger
from services.landingpage_django import CommandRunTarget, LandingpageDjangoService
from services.project_manifest import (
    ProjectManifestContext,
    ProjectManifestError,
    ProjectManifestService,
)
from tools.agent_hints import (
    INSPECT_LANDINGPAGE_MEDIA_INVENTORY_TOOL_DESCRIPTION,
    LIST_LANDINGPAGE_DJANGO_COMMANDS_TOOL_DESCRIPTION,
)
from tools.models import (
    InspectLandingpageMediaInventoryPayload,
    ListLandingpageDjangoCommandsPayload,
)
from utils.mcp_errors import AgentToolErrorResult, build_agent_error_payload

logger: logging.Logger = get_logger("tools.landingpage_django")
landingpage_django_service = LandingpageDjangoService()
project_manifest_service = ProjectManifestService()
READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
)

LANDINGPAGE_DJANGO_COMMANDS_NEXT_STEP_TIPS = [
    "Use command descriptions to choose the next landingpage Django-backed MCP tool.",
    "This tool is discovery only; it does not run arbitrary Django commands.",
    "If the connector is unavailable, verify the generic socket app is running.",
]

LANDINGPAGE_MEDIA_NEXT_STEP_TIPS = [
    "Summarize DB file references, files missing on disk, and disk files not referenced in DB.",
    "Treat review-before-delete entries as review candidates; this tool never deletes media files.",
    "If the connector is unavailable, verify the generic socket app is running.",
]


def _django_commands_error_result(
    *,
    action: str,
    project_name: str,
    error: DockerSocketGatewayError,
) -> ToolResult:
    """Return a stable landingpage Django command discovery error payload."""

    payload = {
        "action": action,
        "requested_project_name": project_name,
        "project_name": project_name,
        **build_agent_error_payload(
            error_code=error.error_code,
            message=error.message,
            retry_tips=[
                "Verify the generic socket app container is running.",
                "Verify MCP can access the shared generic socket app volume.",
                "Verify landingpage provides the mcp_list_commands Django command.",
            ],
            details={"project_name": project_name},
        ),
    }
    return AgentToolErrorResult(
        content=[TextContent(type="text", text=error.message)],
        structured_content=payload,
    )


def _media_inventory_error_result(
    *,
    action: str,
    project_name: str,
    error: DockerSocketGatewayError,
) -> ToolResult:
    """Return a stable media inventory connector error payload."""

    payload = {
        "action": action,
        "requested_project_name": project_name,
        "project_name": project_name,
        **build_agent_error_payload(
            error_code=error.error_code,
            message=error.message,
            retry_tips=[
                "Verify the generic socket app container is running.",
                "Verify MCP can access the shared generic socket app volume.",
                "Verify landingpage provides the media_inventory Django command.",
            ],
            details={"project_name": project_name},
        ),
    }
    return AgentToolErrorResult(
        content=[TextContent(type="text", text=error.message)],
        structured_content=payload,
    )


async def _resolve_landingpage_django_command_run(
    project_name: str,
) -> CommandRunTarget | DockerSocketGatewayError:
    manifest_context = await project_manifest_service.get_or_error(project_name)
    if isinstance(manifest_context, ProjectManifestError):
        return DockerSocketGatewayError(
            message=manifest_context.message,
            error_code="project_manifest_not_found",
        )
    return _landingpage_django_command_run_from_manifest(manifest_context)


def _landingpage_django_command_run_from_manifest(
    manifest_context: ProjectManifestContext,
) -> CommandRunTarget | DockerSocketGatewayError:
    matching_sources = [
        source
        for source in manifest_context.manifest.sources
        if source.source_type == "docker"
        and source.command_run is not None
        and source.command_run.enabled
    ]
    if len(matching_sources) != 1:
        return DockerSocketGatewayError(
            message=(
                "Project manifest must define exactly one docker source with "
                "command_run.enabled set to true."
            ),
            error_code="landingpage_django_source_not_configured",
        )
    source = matching_sources[0]
    assert source.command_run is not None
    return CommandRunTarget(
        container_name=source.target,
        base_command=tuple(source.command_run.base_command),
        cwd=source.command_run.cwd,
    )


@mcp.tool(
    auth=require_scopes(CONTAINER_FILES_READ_SCOPE),
    description=LIST_LANDINGPAGE_DJANGO_COMMANDS_TOOL_DESCRIPTION,
    annotations=READ_ONLY_TOOL_ANNOTATIONS,
)
@project_authorized_tool
async def list_landingpage_django_commands(
    project_name: str | None = None,
) -> ToolResult:
    """List landingpage Django socket commands available to the agent."""

    assert project_name is not None
    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "list_landingpage_django_commands",
            "project_name": project_name,
        },
    )
    command_run = await _resolve_landingpage_django_command_run(project_name)
    if isinstance(command_run, DockerSocketGatewayError):
        return _django_commands_error_result(
            action="list_landingpage_django_commands",
            project_name=project_name,
            error=command_run,
        )
    try:
        commands = landingpage_django_service.list_commands(command_run=command_run)
    except DockerSocketGatewayError as error:
        return _django_commands_error_result(
            action="list_landingpage_django_commands",
            project_name=project_name,
            error=error,
        )

    payload = ListLandingpageDjangoCommandsPayload(
        action="list_landingpage_django_commands",
        requested_project_name=project_name,
        project_name=project_name,
        connector_status="ok",
        report=commands.report,
        next_step_tips=LANDINGPAGE_DJANGO_COMMANDS_NEXT_STEP_TIPS,
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "list_landingpage_django_commands",
            "project_name": project_name,
            "report_keys": sorted(str(key) for key in commands.report.keys()),
        },
    )
    return ToolResult(
        content=[],
        structured_content=payload.model_dump(mode="json"),
    )


@mcp.tool(
    auth=require_scopes(CONTAINER_FILES_READ_SCOPE),
    description=INSPECT_LANDINGPAGE_MEDIA_INVENTORY_TOOL_DESCRIPTION,
    annotations=READ_ONLY_TOOL_ANNOTATIONS,
)
@project_authorized_tool
async def inspect_landingpage_media_inventory(
    project_name: str | None = None,
) -> ToolResult:
    """Inspect landingpage media DB references and disk inventory."""

    assert project_name is not None
    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "inspect_landingpage_media_inventory",
            "project_name": project_name,
        },
    )
    command_run = await _resolve_landingpage_django_command_run(project_name)
    if isinstance(command_run, DockerSocketGatewayError):
        return _media_inventory_error_result(
            action="inspect_landingpage_media_inventory",
            project_name=project_name,
            error=command_run,
        )
    try:
        inventory = landingpage_django_service.inspect_media_inventory(command_run=command_run)
    except DockerSocketGatewayError as error:
        return _media_inventory_error_result(
            action="inspect_landingpage_media_inventory",
            project_name=project_name,
            error=error,
        )

    payload = InspectLandingpageMediaInventoryPayload(
        action="inspect_landingpage_media_inventory",
        requested_project_name=project_name,
        project_name=project_name,
        connector_status="ok",
        report=inventory.report,
        next_step_tips=LANDINGPAGE_MEDIA_NEXT_STEP_TIPS,
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "inspect_landingpage_media_inventory",
            "project_name": project_name,
            "report_keys": sorted(str(key) for key in inventory.report.keys()),
        },
    )
    return ToolResult(
        content=[],
        structured_content=payload.model_dump(mode="json"),
    )
