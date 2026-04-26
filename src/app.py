"""Application setup for the MCP log server."""

from __future__ import annotations

from functools import cache

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider

mcp: FastMCP = FastMCP(name="mcp-log-server")


@cache
def register_mcp_components() -> None:
    """Import MCP component modules once so decorator registration executes."""

    import resources.workflow  # noqa: F401
    import tools.collection  # noqa: F401
    import tools.system  # noqa: F401
    import tools.workflow  # noqa: F401


def create_application(
    auth_provider: AuthProvider | None = None,
) -> FastMCP:
    """Create the FastMCP application."""

    app = mcp
    app.auth = auth_provider

    register_mcp_components()

    return app
