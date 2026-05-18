"""Database service for agent sessions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar, cast

from database.models import AgentSession
from database.schemas import AgentSessionCreate, AgentSessionOut
from database.types import AgentSessionStatus


class AgentSessionService:
    """Wrap ORM access for agent session lifecycle rows."""

    model: ClassVar[type[AgentSession]] = AgentSession

    async def create(self, payload: AgentSessionCreate) -> AgentSessionOut:
        """Create one agent session."""

        obj = await self.model.objects.create(**payload.model_dump())
        return self._to_out(obj)

    async def get_by_name(self, name: str) -> AgentSessionOut | None:
        """Return one session by its agent-facing name."""

        obj = await self.model.objects.filter(name=name).first()
        if obj is None:
            return None
        return self._to_out(obj)

    async def get_or_create(
        self,
        *,
        name: str,
        caller_id: int,
    ) -> AgentSessionOut:
        """Return an existing session or create it for the caller."""

        obj = await self.model.objects.filter(name=name).first()
        if obj is None:
            obj = await self.model.objects.create(
                name=name,
                caller_id=caller_id,
                status=AgentSessionStatus.ACTIVE,
            )
        return self._to_out(obj)

    async def close(self, session_id: int) -> AgentSessionOut:
        """Mark one session as closed."""

        obj = await self.model.objects.get(id=session_id)
        obj.status = AgentSessionStatus.CLOSED
        obj.closed_at = datetime.now(UTC)
        await obj.save(update_fields=["status", "closed_at", "updated_at"])
        return self._to_out(obj)

    @staticmethod
    def _to_out(obj: AgentSession) -> AgentSessionOut:
        """Return the DB OUT pydantic representation for one agent session row."""

        return AgentSessionOut(
            id=obj.id,
            name=obj.name,
            caller_id=cast(int, cast(Any, obj).caller_id),
            status=obj.status,
            closed_at=obj.closed_at,
        )
