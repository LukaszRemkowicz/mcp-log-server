"""Database service for persisted MCP agent call metadata.

AI agent note: keep ORM access in this module routed through the model
``objects`` manager, for example ``AgentCall.objects.get(...)``. Do not call
Tortoise class methods such as ``AgentCall.get(...)`` directly.
"""

from __future__ import annotations

from uuid import UUID

from database.models import AgentCall
from database.services.models import AgentCallCreate, AgentCallFilter, AgentCallUpdate


class AgentCallService:
    """Wrap ORM access for agent call metadata rows."""

    @staticmethod
    async def create(payload: AgentCallCreate) -> AgentCall:
        """Create one metadata row for an MCP agent call or session move."""

        context = payload.model_dump()
        context["id"] = context.pop("pk")
        return await AgentCall.objects.create(**context)

    @staticmethod
    async def get(call_id: UUID) -> AgentCall:
        """Return one agent call row by id."""

        return await AgentCall.objects.get(id=call_id)

    @staticmethod
    async def filter(payload: AgentCallFilter) -> list[AgentCall]:
        """Return agent call rows that match the given filter payload."""

        context = payload.model_dump(exclude={"limit", "offset"}, exclude_none=True)
        return (
            await AgentCall.objects.filter(**context)
            .order_by(
                "created_at",
                "id",
            )
            .offset(payload.offset)
            .limit(payload.limit)
        )

    @staticmethod
    async def update(payload: AgentCallUpdate) -> AgentCall:
        """Update one agent call row with the provided metadata fields."""

        row = await AgentCall.objects.get(id=payload.pk)
        context = payload.model_dump(exclude={"pk"}, exclude_none=True)
        for field_name, field_value in context.items():
            setattr(row, field_name, field_value)
        await row.save(update_fields=[*context])
        return row
