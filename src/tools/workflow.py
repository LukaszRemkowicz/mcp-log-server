"""Workflow-specific MCP tools for the daily log agent."""

from __future__ import annotations

import logging
from typing import TypedDict

from fastmcp.dependencies import CurrentAccessToken, Depends
from fastmcp.server.auth import AccessToken, require_scopes
from fastmcp.tools.base import ToolResult

from auth.scopes import MCP_HEALTH_READ_SCOPE, MCP_STATUS_READ_SCOPE, WORKFLOW_BOOTSTRAP_SCOPE
from dependencies import get_workflow_asset_loader
from logging_config import get_logger
from prompts.workflow import build_daily_log_prompt
from skills.workflow import (
    WorkflowSkillMetadata,
    list_mandatory_workflow_skill_definitions,
    list_optional_workflow_skill_definitions,
)
from tools import mcp
from utils.assets import WorkflowAssetLoader

logger: logging.Logger = get_logger("tools.workflow")


class WorkflowToolMetadata(TypedDict):
    """Describe one MCP tool exposed to the workflow agent.

    This lightweight shape is returned inside the workflow bootstrap payload so
    the agent can tell the LLM which deterministic MCP tools are available
    without embedding the full MCP `tools/list` response.
    """

    tool_name: str
    description: str


class WorkflowVisibleToolDefinition(WorkflowToolMetadata):
    """Internal workflow tool metadata paired with its required scope.

    This is server-side only. It lets the bootstrap builder filter which tools
    should appear in the workflow payload for the current authenticated caller.
    """

    required_scope: str


class WorkflowBootstrapPayload(TypedDict):
    """Structured workflow bootstrap returned to the daily log agent.

    The payload intentionally contains only the information the agent needs to
    start the monitoring workflow:

    - the prepared workflow prompt text
    - the mandatory skill baseline
    - the optional skill inventory
    - the MCP tools visible for the current token

    It does not include raw skill text. Skills are fetched later through MCP
    resources so the workflow can stay token-efficient.
    """

    workflow_name: str
    prompt: str
    mandatory_skills: list[WorkflowSkillMetadata]
    optional_skills: list[WorkflowSkillMetadata]
    tools: list[WorkflowToolMetadata]


WORKFLOW_VISIBLE_TOOL_METADATA: tuple[WorkflowVisibleToolDefinition, ...] = (
    {
        "tool_name": "get_mcp_service_status",
        "description": "Read MCP service status for development/debugging.",
        "required_scope": MCP_STATUS_READ_SCOPE,
    },
    {
        "tool_name": "get_mcp_health_check",
        "description": "Read minimal MCP health state for development/debugging.",
        "required_scope": MCP_HEALTH_READ_SCOPE,
    },
)


def get_allowed_workflow_tool_metadata(
    access_token: AccessToken | None,
) -> list[WorkflowToolMetadata]:
    """Return only the workflow-visible tools allowed for the current token.

    The workflow bootstrap should not advertise tools that the current caller
    cannot use. This helper filters the small workflow-visible tool catalog
    against the scopes already extracted from the authenticated access token.
    """

    if access_token is None:
        return []

    granted_scopes = set(access_token.scopes)
    return [
        {
            "tool_name": metadata["tool_name"],
            "description": metadata["description"],
        }
        for metadata in WORKFLOW_VISIBLE_TOOL_METADATA
        if str(metadata["required_scope"]) in granted_scopes
    ]


def build_workflow_bootstrap_payload(
    asset_loader: WorkflowAssetLoader,
    access_token: AccessToken | None,
) -> WorkflowBootstrapPayload:
    """Assemble the structured bootstrap payload for the workflow agent.

    This is the server-side equivalent of the first workflow step used in the
    existing monitoring flow: prepare the prompt, attach the mandatory and
    optional skill inventory, and describe only the deterministic MCP tools
    the current caller is allowed to use.
    """

    return {
        "workflow_name": "analyze_daily_log_bundle",
        "prompt": build_daily_log_prompt(asset_loader),
        "mandatory_skills": list_mandatory_workflow_skill_definitions(),
        "optional_skills": list_optional_workflow_skill_definitions(),
        "tools": get_allowed_workflow_tool_metadata(access_token),
    }


@mcp.tool(auth=require_scopes(WORKFLOW_BOOTSTRAP_SCOPE))
def analyze_daily_log_bundle(
    asset_loader: WorkflowAssetLoader = Depends(get_workflow_asset_loader),
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Return the workflow bootstrap payload for daily log analysis.

    This is the first tool the workflow agent should call. It returns
    structured content only:

    - the prepared prompt text
    - mandatory skill metadata
    - optional skill metadata
    - the visible tool inventory for the current JWT

    The actual skill content is not embedded here. If the LLM decides it needs
    a skill, the agent should fetch that skill later through `resources/read`
    using the returned `skill://workflow/...` resource URIs.
    """

    logger.info("tool call: analyze_daily_log_bundle")
    return ToolResult(
        content=[],
        structured_content=build_workflow_bootstrap_payload(asset_loader, access_token),
    )
