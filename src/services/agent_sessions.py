"""Application service for explicit interactive agent session lifecycle actions."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastmcp.server.auth import AccessToken
from pydantic import BaseModel, Field
from tortoise.exceptions import BaseORMException

from core.types import LogWorkspace
from database.schemas import AgentCallCreate, AgentCallFilter
from database.services.agent_calls import AgentCallService
from database.types import AgentCallEvent
from logging_config import get_logger

logger = get_logger("services.agent_sessions")


class CloseAgentSessionPayload(BaseModel):
    """Agent-facing success payload for closing one investigation session."""

    action: Literal["close_agent_session"] = "close_agent_session"
    session_id: str
    status: Literal["closed", "already_closed"]
    message: str


class CloseAgentSessionError(BaseModel):
    """Agent-facing error payload for close_agent_session failures."""

    error_code: Literal[
        "session_not_found",
        "session_close_unavailable",
        "workflow_agent_session_close_forbidden",
    ]
    message: str
    retry_tips: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class AgentSessionService:
    """Close interactive investigation sessions through AgentCall metadata."""

    def __init__(self, agent_call_service: AgentCallService | None = None) -> None:
        self.agent_call_service = agent_call_service or AgentCallService()

    async def close_session(
        self,
        *,
        session_id: UUID,
        token: AccessToken,
    ) -> CloseAgentSessionPayload | CloseAgentSessionError:
        """Mark an existing caller-owned interactive session as closed."""

        claims = token.claims
        if token.client_id == "workflow-agent" or claims.get("client_type") == "workflow_agent":
            return CloseAgentSessionError(
                error_code="workflow_agent_session_close_forbidden",
                message="workflow-agent cannot close interactive agent sessions.",
                retry_tips=[
                    "Use a non-workflow agent token for interactive session investigations.",
                    'Use workspace="workflow" for the fixed shared monitoring flow.',
                ],
                details={"session_id": str(session_id)},
            )

        try:
            rows = await self.agent_call_service.filter(
                AgentCallFilter(
                    session_id=session_id,
                    workspace=LogWorkspace.SESSION,
                    limit=1000,
                )
            )
        except BaseORMException:
            logger.exception(
                "failed to load agent session rows for close",
                extra={
                    "event": "agent_session_close_lookup_failed",
                    "session_id": str(session_id),
                    "client_id": token.client_id,
                },
            )
            return CloseAgentSessionError(
                error_code="session_close_unavailable",
                message="Agent session close is temporarily unavailable.",
                retry_tips=[
                    "Retry later or ask administrator to check database connectivity.",
                ],
                details={"session_id": str(session_id)},
            )

        caller_rows = [row for row in rows if row.client_id == token.client_id]
        if not caller_rows:
            return CloseAgentSessionError(
                error_code="session_not_found",
                message="Requested agent session was not found.",
                retry_tips=[
                    "Retry with the exact session_id returned by collect_logs.",
                    "Confirm you are using the same MCP client that created the session.",
                ],
                details={"session_id": str(session_id)},
            )

        if any(row.session_ended for row in caller_rows):
            return CloseAgentSessionPayload(
                session_id=str(session_id),
                status="already_closed",
                message="Agent session was already closed.",
            )

        try:
            await self.agent_call_service.create(
                AgentCallCreate(
                    session_id=session_id,
                    workspace=LogWorkspace.SESSION,
                    event=AgentCallEvent.MCP_CALL_TOOL,
                    session_ended=True,
                    client_id=token.client_id,
                    client_type=claims.get("client_type"),
                    tool_name="close_agent_session",
                    success=True,
                    arguments={"session_id": str(session_id)},
                )
            )
        except BaseORMException:
            logger.exception(
                "failed to write agent session close row",
                extra={
                    "event": "agent_session_close_write_failed",
                    "session_id": str(session_id),
                    "client_id": token.client_id,
                },
            )
            return CloseAgentSessionError(
                error_code="session_close_unavailable",
                message="Agent session close is temporarily unavailable.",
                retry_tips=[
                    "Retry later or ask administrator to check database connectivity.",
                ],
                details={"session_id": str(session_id)},
            )

        return CloseAgentSessionPayload(
            session_id=str(session_id),
            status="closed",
            message="Agent session was closed.",
        )
