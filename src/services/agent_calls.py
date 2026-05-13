"""Application service for AgentCall audit lifecycle logic."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastmcp.server.auth import AccessToken
from pydantic import BaseModel, Field
from tortoise.exceptions import BaseORMException

from database.schemas import AgentCallCreate, AgentCallUpdate
from database.services.agent_calls import AgentCallService as AgentCallDBService
from logging_config import get_logger

logger = get_logger("services.agent_calls")

AGENT_CALL_UNAVAILABLE_RETRY_TIP = (
    "Retry later or ask administrator to check database connectivity, "
    "migrations or any system errors."
)


class AgentCallCreateError(BaseModel):
    """Agent-facing error returned when audit setup blocks tool execution."""

    error_code: Literal["agent_call_unavailable"] = "agent_call_unavailable"
    message: str = "collect_logs is temporarily unavailable."
    retry_tips: list[str] = Field(default_factory=lambda: [AGENT_CALL_UNAVAILABLE_RETRY_TIP])
    details: dict[str, Any] = Field(default_factory=dict)


class AgentCallAuditService:
    """Create and complete AgentCall rows for MCP request handling."""

    def __init__(self, db_service: AgentCallDBService | None = None) -> None:
        self.db_service = db_service or AgentCallDBService()

    async def create_tool_call(
        self,
        *,
        session_id: UUID,
        workspace: str,
        event: str,
        token: AccessToken | None,
        tool_name: str,
        arguments: dict[str, Any] | None,
    ) -> UUID | AgentCallCreateError:
        """Persist the initial AgentCall row for one MCP tool request."""

        claims = token.claims if token is not None else {}
        project_name: str | None = None
        source_keys: list[str] | None = None
        if arguments is not None:
            project_names = arguments.get("project_names")
            if (
                isinstance(project_names, list)
                and len(project_names) == 1
                and isinstance(project_names[0], str)
            ):
                project_name = project_names[0]
            requested_source_keys = arguments.get("source_keys")
            if isinstance(requested_source_keys, list):
                source_keys = [key for key in requested_source_keys if isinstance(key, str)]

        payload: dict[str, Any] = {
            "session_id": session_id,
            "workspace": workspace,
            "event": event,
            "client_id": token.client_id if token is not None else None,
            "client_type": claims.get("client_type"),
            "tool_name": tool_name,
            "project_name": project_name,
            "source_keys": source_keys,
            "arguments": arguments,
        }
        try:
            row = await self.db_service.create(AgentCallCreate(**payload))
        except BaseORMException:
            logger.exception(
                "failed to create agent call audit row",
                extra={
                    "event": "agent_call_create_failed",
                    "tool_name": tool_name,
                    "session_id": str(session_id),
                },
            )
            return AgentCallCreateError(
                details={
                    "tool_name": tool_name,
                    "session_id": str(session_id),
                }
            )
        return row.id

    async def complete_tool_call(
        self,
        *,
        agent_call_pk: UUID | None,
        session_id: UUID | None,
        tool_name: str,
        duration_seconds: float,
        success: bool,
        error_code: str | None,
    ) -> None:
        """Update one AgentCall row with final request outcome metadata."""

        if agent_call_pk is None:
            logger.warning(
                "skipped completing agent call audit row without row pk",
                extra={
                    "event": "agent_call_complete_missing_pk",
                    "tool_name": tool_name,
                    "session_id": str(session_id) if session_id is not None else None,
                    "duration_seconds": duration_seconds,
                    "success": success,
                    "error_code": error_code,
                },
            )
            return

        try:
            await self.db_service.update(
                AgentCallUpdate(
                    pk=agent_call_pk,
                    duration_seconds=duration_seconds,
                    success=success,
                    error_code=error_code,
                )
            )
        except BaseORMException:
            logger.exception(
                "failed to complete agent call audit row",
                extra={
                    "event": "agent_call_complete_failed",
                    "tool_name": tool_name,
                    "session_id": str(session_id) if session_id is not None else None,
                    "agent_call_pk": str(agent_call_pk),
                },
            )
