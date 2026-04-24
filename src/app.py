"""Application setup for the MCP log server."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider

from tools import mcp


def create_application(
    auth_provider: AuthProvider | None = None,
) -> FastMCP:
    """Create the FastMCP application."""

    app = mcp
    app.auth = auth_provider

    # These imports intentionally happen during app creation because the
    # modules register MCP tools/resources via decorators and module-level setup
    # side effects. Keeping them here makes the registration step explicit and
    # avoids triggering it earlier just by importing app.py.
    import resources.workflow  # noqa: F401
    import tools.system  # noqa: F401
    import tools.workflow  # noqa: F401

    return app
