"""Workflow-specific MCP tools for the daily log agent.

This module owns the workflow bootstrap entrypoint:

- `analyze_daily_log_bundle`

That tool is the first MCP call the fixed workflow agent should make. Its job
is not to collect logs directly. Instead, it prepares the workflow bootstrap
payload:

- the main prompt text
- the mandatory and optional workflow skill inventory
- a curated list of follow-up MCP tools the workflow can use after bootstrap

Important distinction:

- `analyze_daily_log_bundle` itself is a normal MCP tool registered with
  `@mcp.tool(...)`
- the `tools` field returned inside its payload is built only from tools
  registered with `@workflow_discoverable_tool(...)`
- `@workflow_discoverable_tool(...)` does two things:
  - registers a normal MCP tool
  - also adds that tool to the workflow bootstrap inventory

Why that split exists:

- some MCP tools are general-purpose and should exist in `tools/list`
- only a smaller subset should be highlighted inside the workflow bootstrap
  response as the recommended post-bootstrap tool inventory

So this module does not "discover" tools by scanning files. It reads the
shared in-memory registry populated earlier by `@workflow_discoverable_tool(...)`
decorators when sibling tool modules were imported during app startup.
"""

from __future__ import annotations

import logging
from typing import TypedDict

from fastmcp.dependencies import CurrentAccessToken, Depends
from fastmcp.server.auth import AccessToken, require_scopes
from fastmcp.tools.base import ToolResult

from app import mcp, register_mcp_components
from auth.scopes import WORKFLOW_BOOTSTRAP_SCOPE
from dependencies import get_workflow_asset_loader
from logging_config import get_logger
from prompts.workflow import build_daily_log_prompt
from skills.workflow import (
    WorkflowSkillMetadata,
    list_mandatory_workflow_skill_definitions,
    list_optional_workflow_skill_definitions,
)
from tools.registry import list_workflow_discoverable_tool_registrations
from utils.assets import WorkflowAssetLoader

logger: logging.Logger = get_logger("tools.workflow")


class WorkflowToolMetadata(TypedDict):
    """Describe one follow-up MCP tool exposed to the workflow agent.

    This lightweight shape is returned inside the workflow bootstrap payload so
    the agent can tell the LLM which deterministic MCP tools are available
    without embedding the full MCP `tools/list` response.

    These entries are not the same thing as "all workflow-related tools". They
    specifically represent the curated subset of tools registered through
    `@workflow_discoverable_tool(...)` that should be advertised after the
    bootstrap step has already happened.
    """

    tool_name: str
    description: str


class WorkflowBootstrapPayload(TypedDict):
    """Structured workflow bootstrap returned to the daily log agent.

    The payload intentionally contains only the information the agent needs to
    start the monitoring workflow:

    - the prepared workflow prompt text
    - the mandatory skill baseline
    - the optional skill inventory
    - the MCP tools visible for the current token and marked as
      workflow-discoverable

    It does not include raw skill text. Skills are fetched later through MCP
    resources so the workflow can stay token-efficient.

    It also does not need to include the bootstrap tool itself. The caller has
    already used `analyze_daily_log_bundle` to obtain this payload. The `tools`
    field is meant to describe the next deterministic MCP actions available to
    the workflow agent.
    """

    workflow_name: str
    prompt: str
    mandatory_skills: list[WorkflowSkillMetadata]
    optional_skills: list[WorkflowSkillMetadata]
    tools: list[WorkflowToolMetadata]


def get_allowed_workflow_tool_metadata(
    access_token: AccessToken | None,
) -> list[WorkflowToolMetadata]:
    """Return workflow-discoverable follow-up tools allowed for the current token.

    The workflow bootstrap should not advertise tools that the current caller
    cannot use. This helper filters the small workflow-discoverable tool catalog
    against the scopes already extracted from the authenticated access token.

    "Discoverable" here means:

    - the tool was registered with `@workflow_discoverable_tool(...)`
    - its module was imported during app registration
    - its required scope is present in the current JWT

    This helper does not inspect the filesystem or ask FastMCP for a dynamic
    list of all tools. It reads the shared registry populated at import time by
    the decorator.
    """

    if access_token is None:
        return []

    # Ensure sibling MCP components have registered before reading the shared
    # workflow-discoverable registry. This keeps direct bootstrap tests aligned with
    # the fully assembled app without relying on local import side effects here.
    register_mcp_components()

    granted_scopes = set(access_token.scopes)
    return [
        {
            "tool_name": metadata["tool_name"],
            "description": metadata["description"],
        }
        for metadata in list_workflow_discoverable_tool_registrations()
        if str(metadata["required_scope"]) in granted_scopes
    ]


def build_workflow_bootstrap_payload(
    asset_loader: WorkflowAssetLoader,
    access_token: AccessToken | None,
) -> WorkflowBootstrapPayload:
    """Assemble the structured bootstrap payload for the workflow agent.

    This is the server-side equivalent of the first workflow step used in the
    existing monitoring flow: prepare the prompt, attach the mandatory and
    optional skill inventory, and describe only the deterministic MCP follow-up
    tools the current caller is allowed to use after bootstrap.
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
    - the workflow-discoverable follow-up tool inventory for the current JWT

    The actual skill content is not embedded here. If the LLM decides it needs
    a skill, the agent should fetch that skill later through `resources/read`
    using the returned `skill://workflow/...` resource URIs.

    The returned `tools` field is intentionally a curated subset, not the full
    `tools/list` response. It highlights the deterministic MCP actions the
    workflow should consider after bootstrap, while the broader MCP surface
    still remains available through normal discovery calls like `tools/list`.
    """

    logger.info("tool call: analyze_daily_log_bundle")
    return ToolResult(
        content=[],
        structured_content=build_workflow_bootstrap_payload(asset_loader, access_token),
    )
