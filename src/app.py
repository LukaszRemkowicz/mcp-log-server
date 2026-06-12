"""Application setup for the MCP log server."""

from __future__ import annotations

from functools import cache

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from database.lifecycle import database_lifespan
from middleware.audit import AccessAuditMiddleware
from middleware.authorized_manifests import AuthorizedManifestsMiddleware

mcp: FastMCP = FastMCP(name="mcp-log-server", lifespan=database_lifespan)


@cache
def register_mcp_components() -> None:
    """Import MCP component modules once so decorator registration executes."""

    import resources.workflow  # noqa: F401
    import tools.analysis  # noqa: F401
    import tools.collection  # noqa: F401
    import tools.container_inspection  # noqa: F401
    import tools.fail2ban  # noqa: F401
    import tools.sessions  # noqa: F401
    import tools.snapshots  # noqa: F401
    import tools.system  # noqa: F401
    import tools.workflow  # noqa: F401


@cache
def register_mcp_middleware() -> None:
    """Attach application middleware once."""

    mcp.add_middleware(AccessAuditMiddleware())
    mcp.add_middleware(AuthorizedManifestsMiddleware())


@cache
def register_http_routes() -> None:
    """Attach non-MCP HTTP routes used by runtime infrastructure."""

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(request: Request) -> Response:  # noqa: ARG001
        return JSONResponse({"status": "ok"})


def create_application(
    auth_provider: AuthProvider | None = None,
) -> FastMCP:
    """Create the FastMCP application."""

    app = mcp
    app.auth = auth_provider

    register_http_routes()
    register_mcp_middleware()
    register_mcp_components()

    return app
