"""Database service for manually managed MCP caller allowlist rows."""

from __future__ import annotations

from typing import ClassVar

from core.types import LogWorkspace
from database.models import Authentication


class AuthenticationService:
    """Wrap ORM access for Authentication rows."""

    model: ClassVar[type[Authentication]] = Authentication

    async def get_allowed(
        self,
        *,
        client_id: str,
        client_type: str,
        workspace: LogWorkspace,
    ) -> Authentication | None:
        """Return a matching allowed MCP caller row, if one exists."""

        return await (
            self.model.objects.filter(
                client_id=client_id,
                client_type=client_type,
                workspace=workspace,
            )
            .limit(1)
            .first()
        )
