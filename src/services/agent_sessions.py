"""Application service for interactive agent session lifecycle actions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from tortoise.exceptions import BaseORMException

from database.schemas import AgentSessionOut
from database.services.agent_sessions import AgentSessionService as AgentSessionDBService
from logging_config import get_logger

logger = get_logger("services.agent_sessions")


class AgentSessionLookupError(BaseModel):
    """Service-level error returned when session rows cannot be loaded."""

    error_code: Literal["session_close_unavailable"]
    message: str
    retry_tips: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class AgentSessionService:
    """Read and update interactive investigation session metadata."""

    def __init__(self, db_service: AgentSessionDBService | None = None) -> None:
        self.db_service = db_service or AgentSessionDBService()

    async def load_session(
        self,
        *,
        name: str,
    ) -> AgentSessionOut | None | AgentSessionLookupError:
        """Load persisted state for one interactive session."""

        try:
            return await self.db_service.get_by_name(name)
        except BaseORMException:
            logger.exception(
                "failed to load agent session for close",
                extra={
                    "event": "agent_session_close_lookup_failed",
                    "name": name,
                },
            )
            return AgentSessionLookupError(
                error_code="session_close_unavailable",
                message="Agent session close is temporarily unavailable.",
                retry_tips=[
                    "Retry later or ask administrator to check database connectivity.",
                ],
                details={"session_id": name},
            )

    async def close_session(self, *, session_id: int) -> AgentSessionOut | AgentSessionLookupError:
        """Mark one interactive session as closed."""

        try:
            return await self.db_service.close(session_id)
        except BaseORMException:
            logger.exception(
                "failed to close agent session",
                extra={
                    "event": "agent_session_close_write_failed",
                    "session_id": session_id,
                },
            )
            return AgentSessionLookupError(
                error_code="session_close_unavailable",
                message="Agent session close is temporarily unavailable.",
                retry_tips=[
                    "Retry later or ask administrator to check database connectivity.",
                ],
                details={},
            )
