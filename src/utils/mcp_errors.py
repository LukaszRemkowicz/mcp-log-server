"""Shared agent-facing MCP error payloads and tool results."""

from __future__ import annotations

from typing import Any, Literal, TypedDict, cast

from fastmcp.resources.base import ResourceContent, ResourceResult
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolResult, TextContent

from utils.types import JSONObject


class AgentErrorPayload(TypedDict):
    """Structured MCP error payload returned to agents for recoverable failures."""

    status: Literal["error"]
    error_code: str
    message: str
    retry_tips: list[str]
    details: JSONObject


class AgentToolErrorResult(ToolResult):
    """Tool result that preserves MCP `isError=true` and structured agent guidance."""

    def __init__(
        self,
        *,
        content: list[TextContent],
        structured_content: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Mirror the ToolResult constructor with an explicit typed signature for IDEs."""

        super().__init__(
            content=content,
            structured_content=structured_content,
            meta=meta,
        )

    def to_mcp_result(self) -> CallToolResult:
        result: CallToolResult = CallToolResult(
            content=self.content,
            structuredContent=self.structured_content,
            isError=True,
        )
        if self.meta is not None:
            result.meta = self.meta
        return result


def build_agent_error_payload(
    *,
    error_code: str,
    message: str,
    retry_tips: list[str],
    details: JSONObject | None = None,
) -> AgentErrorPayload:
    """Build one shared agent-first MCP error payload."""

    safe_details: JSONObject = details if details is not None else cast(JSONObject, {})
    return {
        "status": "error",
        "error_code": error_code,
        "message": message,
        "retry_tips": retry_tips,
        "details": safe_details,
    }


def build_agent_tool_error_result(
    *,
    error_code: str,
    message: str,
    retry_tips: list[str],
    details: JSONObject | None = None,
) -> AgentToolErrorResult:
    """Create an MCP tool error result with structured retry guidance."""

    payload = build_agent_error_payload(
        error_code=error_code,
        message=message,
        retry_tips=retry_tips,
        details=details,
    )
    return AgentToolErrorResult(
        content=[TextContent(type="text", text=message)],
        structured_content=cast(dict[str, Any], payload),
    )


def build_agent_resource_error_result(
    *,
    error_code: str,
    message: str,
    retry_tips: list[str],
    details: JSONObject | None = None,
) -> ResourceResult:
    """Create a JSON resource result with the shared agent error payload."""

    payload = build_agent_error_payload(
        error_code=error_code,
        message=message,
        retry_tips=retry_tips,
        details=details,
    )
    return ResourceResult(
        contents=[
            ResourceContent(
                payload,
                mime_type="application/json",
            )
        ]
    )
