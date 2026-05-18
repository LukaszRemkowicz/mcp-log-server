"""Interactive agent session lifecycle MCP tools."""

from __future__ import annotations

from fastmcp.server.auth import require_scopes
from fastmcp.tools.base import ToolResult
from tortoise.exceptions import BaseORMException

from app import mcp
from auth.mcp_caller_context import get_request_mcp_caller
from auth.scopes import SESSION_CLOSE_SCOPE
from database.schemas import AgentCallCreate
from database.services.agent_calls import AgentCallService
from database.types import AgentCallEvent, AgentSessionStatus
from logging_config import get_logger
from services.agent_sessions import AgentSessionLookupError, AgentSessionService
from services.session_ids import SESSION_ID_MAX_LENGTH
from tools.agent_hints import CLOSE_AGENT_SESSION_TOOL_DESCRIPTION
from utils.mcp_errors import build_agent_tool_error_result

logger = get_logger("tools.sessions")

agent_session_service = AgentSessionService()
agent_call_service = AgentCallService()


@mcp.tool(
    auth=require_scopes(SESSION_CLOSE_SCOPE),
    description=CLOSE_AGENT_SESSION_TOOL_DESCRIPTION,
)
async def close_agent_session(
    session_id: str,
) -> ToolResult:
    """Close one interactive investigation session without deleting snapshots."""

    caller = get_request_mcp_caller()
    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        return build_agent_tool_error_result(
            error_code="invalid_session_id",
            message="session_id must be a non-empty string.",
            retry_tips=["Retry with the exact session_id returned by collect_logs."],
            details={"session_id": session_id},
        )
    if len(normalized_session_id) > SESSION_ID_MAX_LENGTH:
        return build_agent_tool_error_result(
            error_code="invalid_session_id",
            message=f"session_id must be {SESSION_ID_MAX_LENGTH} characters or fewer.",
            retry_tips=["Retry with the exact session_id returned by collect_logs."],
            details={"session_id": session_id, "max_length": SESSION_ID_MAX_LENGTH},
        )

    if caller.client_id == "workflow-agent" or caller.client_type == "workflow_agent":
        return build_agent_tool_error_result(
            error_code="workflow_agent_session_close_forbidden",
            message="workflow-agent cannot close interactive agent sessions.",
            retry_tips=[
                "Use a non-workflow agent token for interactive session investigations.",
                'Use workspace="workflow" for the fixed shared monitoring flow.',
            ],
            details={"session_id": normalized_session_id},
        )

    session = await agent_session_service.load_session(
        name=normalized_session_id,
    )
    if isinstance(session, AgentSessionLookupError):
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": "close_agent_session",
                "error_code": session.error_code,
                "session_id": normalized_session_id,
            },
        )
        return build_agent_tool_error_result(
            error_code=session.error_code,
            message=session.message,
            retry_tips=session.retry_tips,
            details=session.details,
        )
    if session is None or session.caller_id != caller.caller_id:
        return build_agent_tool_error_result(
            error_code="session_not_found",
            message="Requested agent session was not found.",
            retry_tips=[
                "Retry with the exact session_id returned by collect_logs.",
                "Confirm you are using the same MCP client that created the session.",
            ],
            details={"session_id": normalized_session_id},
        )

    if session.status == AgentSessionStatus.CLOSED:
        return ToolResult(
            content=[],
            structured_content={
                "action": "close_agent_session",
                "session_id": normalized_session_id,
                "status": "already_closed",
                "message": "Agent session was already closed.",
            },
        )

    close_result = await agent_session_service.close_session(session_id=session.id)
    if isinstance(close_result, AgentSessionLookupError):
        return build_agent_tool_error_result(
            error_code=close_result.error_code,
            message=close_result.message,
            retry_tips=close_result.retry_tips,
            details=close_result.details,
        )

    try:
        await agent_call_service.create(
            AgentCallCreate(
                session_id=session.id,
                caller=caller.caller_id,
                event=AgentCallEvent.MCP_CALL_TOOL,
                tool_name="close_agent_session",
                success=True,
                arguments={"session_id": normalized_session_id},
            )
        )
    except BaseORMException:
        logger.exception(
            "failed to write agent session close row",
            extra={
                "event": "agent_session_close_write_failed",
                "session_id": normalized_session_id,
                "client_id": caller.client_id,
            },
        )
        return build_agent_tool_error_result(
            error_code="session_close_unavailable",
            message="Agent session close is temporarily unavailable.",
            retry_tips=[
                "Retry later or ask administrator to check database connectivity.",
            ],
            details={"session_id": normalized_session_id},
        )

    result = {
        "action": "close_agent_session",
        "session_id": normalized_session_id,
        "status": "closed",
        "message": "Agent session was closed.",
    }
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "close_agent_session",
            "session_id": result["session_id"],
            "status": result["status"],
        },
    )
    return ToolResult(content=[], structured_content=result)
