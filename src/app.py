"""Application setup for the MCP log server."""

from __future__ import annotations

from functools import cache

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider

from database.lifecycle import database_lifespan
from middleware.audit import AccessAuditMiddleware

mcp: FastMCP = FastMCP(name="mcp-log-server", lifespan=database_lifespan)


@cache
def register_mcp_components() -> None:
    """Import MCP component modules once so decorator registration executes."""

    import resources.workflow  # noqa: F401
    import tools.analysis  # noqa: F401
    import tools.collection  # noqa: F401
    import tools.container_inspection  # noqa: F401
    import tools.snapshots  # noqa: F401
    import tools.system  # noqa: F401
    import tools.workflow  # noqa: F401


@cache
def register_mcp_middleware() -> None:
    """Attach application middleware once."""

    mcp.add_middleware(AccessAuditMiddleware())


def create_application(
    auth_provider: AuthProvider | None = None,
) -> FastMCP:
    """Create the FastMCP application."""

    app = mcp
    app.auth = auth_provider

    register_mcp_middleware()
    register_mcp_components()

    return app
