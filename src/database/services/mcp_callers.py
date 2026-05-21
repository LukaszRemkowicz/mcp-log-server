"""Database service for manually managed MCP caller allowlist rows."""

from __future__ import annotations

from auth.mcp_caller_model import get_mcp_caller_model
from core.types import LogWorkspace
from database.models import McpCaller


class McpCallerService:
    """Wrap ORM access for McpCaller rows."""

    @property
    def model(self) -> type[McpCaller]:
        """Return the configured MCP caller model."""

        return get_mcp_caller_model()

    async def get_allowed(
        self,
        *,
        client_id: str,
        client_type: str,
        workspace: LogWorkspace,
    ) -> McpCaller | None:
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

    async def get_allowed_by_identity(
        self,
        *,
        client_id: str,
        client_type: str,
    ) -> McpCaller | None:
        """Return a matching allowed MCP caller row by authenticated identity."""

        return await (
            self.model.objects.filter(
                client_id=client_id,
                client_type=client_type,
            )
            .order_by("workspace", "id")
            .limit(1)
            .first()
        )
