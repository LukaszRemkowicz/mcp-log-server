"""Normalized agent-facing error mapping for MCP tool modules.

This module keeps the rule tables and payload builders that translate internal
validation/runtime failures into stable MCP error contracts for agents.

Why this file exists:

- tool modules should stay focused on public MCP behavior
- error mapping tables otherwise grow into distracting policy ladders inline
- tests can target normalized error behavior without importing the whole tool
  implementation surface
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastmcp.server.auth import AccessToken
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from settings import Settings
from tools.models import SnapshotWorkspace
from utils.mcp_errors import (
    AgentToolErrorResult,
    build_agent_error_payload,
    build_agent_tool_error_result,
)
from utils.types import JSONObject


@dataclass(frozen=True)
class CollectLogsErrorRule:
    """Describe one ordered message-to-error mapping for collect_logs failures."""

    pattern: str
    error_code: str


COLLECT_LOGS_ERROR_RULES: tuple[CollectLogsErrorRule, ...] = (
    CollectLogsErrorRule("project_key claim", "missing_project_key_claim"),
    CollectLogsErrorRule("authorized by the access token", "project_access_mismatch"),
    CollectLogsErrorRule("No manifest file was found", "unknown_project"),
    CollectLogsErrorRule("loaded manifest project_key", "manifest_project_mismatch"),
    CollectLogsErrorRule("Invalid docker time filter", "invalid_docker_time_filter"),
    CollectLogsErrorRule("session_id is required", "missing_session_id"),
    CollectLogsErrorRule(
        "validation error for LogSnapshotMetadata",
        "invalid_snapshot_metadata",
    ),
)


def classify_collect_logs_error(message: str) -> str:
    """Map one collect_logs validation message to a stable agent-facing error code."""

    for rule in COLLECT_LOGS_ERROR_RULES:
        if rule.pattern in message:
            return rule.error_code
    return "collect_logs_validation_error"


def build_collect_logs_error_retry_tips(error_code: str) -> list[str]:
    """Return caller guidance for one normalized collect_logs error code."""

    if error_code == "missing_project_key_claim":
        return [
            "Retry with a JWT that includes the project_key claim for the monitored project.",
            "Use get_mcp_service_status to inspect the current caller context if needed.",
        ]
    if error_code == "project_access_mismatch":
        return [
            "Retry with project_name equal to the project_key authorized by the current JWT.",
            "Use get_mcp_service_status to confirm the current project_key before retrying.",
        ]
    if error_code == "unknown_project":
        return [
            "Call list_projects to discover the project_name values currently available.",
            "Retry with one of the listed project names.",
        ]
    if error_code == "manifest_project_mismatch":
        return [
            "Verify that the manifest filename and its project_key describe the same project.",
            "Retry only after fixing the inconsistent manifest configuration.",
        ]
    if error_code == "invalid_docker_time_filter":
        return [
            "Retry with since/until as ISO-8601, unix seconds, or a duration like 30m, 1h, or 1d.",
            "Omit since/until if you want the current default collection range.",
        ]
    if error_code == "missing_session_id":
        return [
            "Retry with session_id set when workspace='session'.",
            "Reuse the same session_id for later collect_logs calls in the same agent session.",
        ]
    if error_code == "invalid_snapshot_metadata":
        return [
            "Remove or migrate incompatible persisted workflow snapshots, then retry.",
            (
                "If you expect a clean run, clear the workflow snapshot directory "
                "before collecting again."
            ),
        ]
    return [
        "Review the collect_logs arguments and retry with a valid project_name and source_keys.",
    ]


def build_collect_logs_error_details(
    error_code: str,
    *,
    settings: Settings,
    access_token: AccessToken,
    project_name: str | None,
    workspace: SnapshotWorkspace,
    session_id: str | None,
) -> JSONObject | None:
    """Return structured context for one normalized collect_logs validation error."""

    if error_code == "project_access_mismatch":
        return {
            "requested_project_name": project_name,
            "authorized_project_name": str(access_token.claims.get("project_key") or ""),
        }
    if error_code in {"unknown_project", "manifest_project_mismatch"}:
        return {
            "requested_project_name": project_name,
            "manifests_dir": str(settings.MANIFEST_PATH.parent),
        }
    if error_code == "missing_session_id":
        return {
            "workspace": workspace,
            "session_id": session_id,
        }
    if error_code == "invalid_snapshot_metadata":
        return {
            "workspace": workspace,
            "project_name": project_name,
            "logs_dir": str(settings.LOGS_DIR),
        }
    return None


def render_collect_logs_error_message(error_code: str, message: str) -> str:
    """Return a human-readable collect_logs error message for agents.

    Some low-level validation errors, especially persisted metadata schema
    mismatches, are too implementation-specific to be useful as raw text for
    an agent or operator. This helper keeps the public MCP error message
    understandable while preserving the detailed cause in `details`/logs.
    """

    if error_code == "invalid_snapshot_metadata":
        return (
            "Persisted workflow snapshot metadata is incompatible with the current "
            "schema. Clear or migrate the existing workflow snapshots and retry."
        )
    return message


def build_collect_logs_error_result(
    message: str,
    *,
    settings: Settings,
    access_token: AccessToken,
    project_name: str | None,
    workspace: SnapshotWorkspace,
    session_id: str | None,
) -> ToolResult:
    """Translate one collect_logs validation error into a stable MCP tool result."""

    error_code = classify_collect_logs_error(message)
    return build_agent_tool_error_result(
        error_code=error_code,
        message=render_collect_logs_error_message(error_code, message),
        retry_tips=build_collect_logs_error_retry_tips(error_code),
        details=build_collect_logs_error_details(
            error_code,
            settings=settings,
            access_token=access_token,
            project_name=project_name,
            workspace=workspace,
            session_id=session_id,
        ),
    )


@dataclass(frozen=True)
class ContainerInspectionErrorRule:
    """Describe one ordered message-to-error mapping for inspection failures."""

    message_fragment: str
    error_code: str
    retry_tips: list[str]


CONTAINER_INSPECTION_ERROR_RULES: tuple[ContainerInspectionErrorRule, ...] = (
    ContainerInspectionErrorRule(
        message_fragment="project_key claim",
        error_code="missing_project_key_claim",
        retry_tips=[
            "Retry with a JWT that includes the project_key claim for the monitored project.",
        ],
    ),
    ContainerInspectionErrorRule(
        message_fragment="authorized by the access token",
        error_code="project_access_mismatch",
        retry_tips=[
            "Retry with project_name equal to the project_key authorized by the current JWT.",
        ],
    ),
    ContainerInspectionErrorRule(
        message_fragment="No manifest file was found",
        error_code="unknown_project",
        retry_tips=[
            "Call list_projects to discover the project_name values currently available.",
            "Retry with one of the listed project names.",
        ],
    ),
    ContainerInspectionErrorRule(
        message_fragment="loaded manifest project_key",
        error_code="manifest_project_mismatch",
        retry_tips=[
            "Verify that the manifest filename and its project_key describe the same project.",
        ],
    ),
    ContainerInspectionErrorRule(
        message_fragment="source_key was not found",
        error_code="unknown_container_source_key",
        retry_tips=[
            "Retry with one of the docker source_keys returned by list_projects for this project.",
        ],
    ),
    ContainerInspectionErrorRule(
        message_fragment="only available for docker sources",
        error_code="container_source_type_mismatch",
        retry_tips=["Retry with a docker-backed source_key."],
    ),
    ContainerInspectionErrorRule(
        message_fragment="not enabled for the requested source",
        error_code="container_inspection_not_enabled",
        retry_tips=[
            "Retry with a source that exposes inspect_path_prefixes in the project manifest.",
        ],
    ),
    ContainerInspectionErrorRule(
        message_fragment="must be an absolute path",
        error_code="container_path_not_absolute",
        retry_tips=["Retry with an absolute container path like /app/VERSION."],
    ),
    ContainerInspectionErrorRule(
        message_fragment="parent directory traversal",
        error_code="container_path_parent_traversal",
        retry_tips=["Retry with a normalized path inside the allowed source prefix."],
    ),
    ContainerInspectionErrorRule(
        message_fragment="outside the manifest whitelist",
        error_code="container_path_not_allowed",
        retry_tips=[
            "Retry with a path under one of the manifest-approved path prefixes for this source.",
        ],
    ),
    ContainerInspectionErrorRule(
        message_fragment="Docker Engine API is not available",
        error_code="docker_api_unavailable",
        retry_tips=["Retry in a runtime where the Docker socket is mounted and reachable."],
    ),
    ContainerInspectionErrorRule(
        message_fragment="was not found",
        error_code="container_path_not_found",
        retry_tips=["Retry with a different path under the allowed source prefixes."],
    ),
)


def build_container_file_error_details(
    *,
    error_code: str,
    requested_project_name: str | None,
    source_key: str | None,
    path: str | None,
    access_token: AccessToken | None,
    settings: Settings,
) -> dict[str, Any] | None:
    """Build structured details for one normalized inspection error code."""

    if error_code == "project_access_mismatch":
        return {
            "requested_project_name": requested_project_name,
            "authorized_project_name": str(
                access_token.claims.get("project_key") if access_token is not None else ""
            ),
        }
    if error_code == "unknown_project":
        return {"requested_project_name": requested_project_name}
    if error_code == "manifest_project_mismatch":
        return {"manifests_dir": str(settings.MANIFEST_PATH.parent)}
    if error_code in {
        "unknown_container_source_key",
        "container_source_type_mismatch",
        "container_inspection_not_enabled",
    }:
        return {"source_key": source_key}
    if error_code in {"container_path_not_absolute", "container_path_parent_traversal"}:
        return {"path": path}
    if error_code in {"container_path_not_allowed", "container_path_not_found"}:
        return {"source_key": source_key, "path": path}
    return None


def classify_container_file_error(message: str) -> tuple[str, list[str]]:
    """Classify one inspection error message into a stable code and retry tips."""

    for rule in CONTAINER_INSPECTION_ERROR_RULES:
        if rule.message_fragment in message:
            return rule.error_code, rule.retry_tips
    return (
        "container_file_inspection_error",
        ["Review the tool arguments and retry with a valid source_key and path."],
    )


def build_container_file_error_result(
    *,
    action: str,
    message: str,
    requested_project_name: str | None,
    source_key: str | None,
    path: str | None,
    access_token: AccessToken | None,
    settings: Settings,
    shape_defaults: dict[str, Any] | None = None,
) -> ToolResult:
    """Map one inspection failure into a stable, agent-facing MCP error result."""

    error_code, retry_tips = classify_container_file_error(message)
    details = build_container_file_error_details(
        error_code=error_code,
        requested_project_name=requested_project_name,
        source_key=source_key,
        path=path,
        access_token=access_token,
        settings=settings,
    )

    payload = {
        "action": action,
        **(shape_defaults or {}),
        **build_agent_error_payload(
            error_code=error_code,
            message=message,
            retry_tips=retry_tips,
            details=details,
        ),
    }
    return AgentToolErrorResult(
        content=[TextContent(type="text", text=message)],
        structured_content=payload,
    )
