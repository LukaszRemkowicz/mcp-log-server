"""Interactive agent session lifecycle MCP tools."""

from __future__ import annotations

from uuid import UUID

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken, require_scopes
from fastmcp.tools.base import ToolResult

from app import mcp
from auth.scopes import SESSION_CLOSE_SCOPE
from logging_config import get_logger
from services.agent_sessions import AgentSessionService, CloseAgentSessionError
from tools.agent_hints import CLOSE_AGENT_SESSION_TOOL_DESCRIPTION
from utils.mcp_errors import build_agent_tool_error_result

logger = get_logger("tools.sessions")

agent_session_service = AgentSessionService()


@mcp.tool(
    auth=require_scopes(SESSION_CLOSE_SCOPE),
    description=CLOSE_AGENT_SESSION_TOOL_DESCRIPTION,
)
async def close_agent_session(
    session_id: str,
    access_token: AccessToken | None = CurrentAccessToken(),
) -> ToolResult:
    """Close one interactive investigation session without deleting snapshots."""

    assert access_token is not None
    try:
        parsed_session_id = UUID(session_id)
    except (TypeError, ValueError):
        return build_agent_tool_error_result(
            error_code="invalid_session_id",
            message="session_id must be a valid UUID.",
            retry_tips=["Retry with the exact session_id returned by collect_logs."],
            details={"session_id": session_id},
        )

    result = await agent_session_service.close_session(
        session_id=parsed_session_id,
        token=access_token,
    )
    if isinstance(result, CloseAgentSessionError):
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": "close_agent_session",
                "error_code": result.error_code,
                "session_id": str(parsed_session_id),
            },
        )
        return build_agent_tool_error_result(
            error_code=result.error_code,
            message=result.message,
            retry_tips=result.retry_tips,
            details=result.details,
        )

    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "close_agent_session",
            "session_id": result.session_id,
            "status": result.status,
        },
    )
    return ToolResult(content=[], structured_content=result.model_dump(mode="json"))
