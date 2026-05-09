"""Database service for persisted MCP agent call metadata.

AI agent note: keep ORM access in this module routed through the model
``objects`` manager, for example ``AgentCall.objects.get(...)``. Do not call
Tortoise class methods such as ``AgentCall.get(...)`` directly.
"""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID

from database.models import AgentCall
from database.services.models import AgentCallCreate, AgentCallFilter, AgentCallUpdate


class AgentCallService:
    """Wrap ORM access for agent call metadata rows."""

    model: ClassVar[type[AgentCall]] = AgentCall

    async def create(self, payload: AgentCallCreate) -> AgentCall:
        """Create one metadata row for an MCP agent call or session move."""

        context = payload.model_dump()
        context["id"] = context.pop("pk")
        return await self.model.objects.create(**context)

    async def get(self, call_id: UUID) -> AgentCall:
        """Return one agent call row by id."""

        return await self.model.objects.get(id=call_id)

    async def filter(self, payload: AgentCallFilter) -> list[AgentCall]:
        """Return agent call rows that match the given filter payload."""

        context = payload.model_dump(exclude={"limit", "offset"}, exclude_none=True)
        return (
            await self.model.objects.filter(**context)
            .order_by(
                "created_at",
                "id",
            )
            .offset(payload.offset)
            .limit(payload.limit)
        )

    async def update(self, payload: AgentCallUpdate) -> AgentCall:
        """Update one agent call row with the provided metadata fields."""

        row = await self.model.objects.get(id=payload.pk)
        context = payload.model_dump(exclude={"pk"}, exclude_none=True)
        for field_name, field_value in context.items():
            setattr(row, field_name, field_value)
        await row.save(update_fields=[*context])
        return row
