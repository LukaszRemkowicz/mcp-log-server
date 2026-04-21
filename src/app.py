"""Application setup for the MCP log server."""

from __future__ import annotations

from fastmcp import FastMCP

from auth.base import AuthProvider
from auth.providers.allow_all import AllowAllAuthProvider
from settings import Settings, get_settings


def build_health_payload() -> dict[str, object]:
    """Return a minimal service health payload."""

    return {
        "status": "ok",
    }


def service_status(settings: Settings, auth_context_subject: str) -> dict[str, object]:
    """Return service status for MCP callers."""

    return {
        "name": "mcp-log-server",
        "status": "ok",
        "subject": auth_context_subject,
        "environment": settings.environment,
        "host": settings.host,
        "port": settings.port,
        "log_level": settings.log_level,
    }


def health_check() -> dict[str, object]:
    """Return service health for MCP callers."""

    return build_health_payload()


def register_tools(app: FastMCP, settings: Settings, auth_provider: AuthProvider) -> None:
    """Register MCP tools on the application instance."""

    auth_context = auth_provider.authenticate()

    def service_status_tool() -> dict[str, object]:
        return service_status(settings, auth_context.subject)

    app.tool(service_status_tool, name="service_status")
    app.tool(health_check, name="health_check")


def create_application(
    settings: Settings | None = None,
    auth_provider: AuthProvider | None = None,
) -> object:
    """Create the FastMCP application."""

    resolved_settings = settings or get_settings()
    resolved_auth_provider = auth_provider or AllowAllAuthProvider()

    app = FastMCP(name="mcp-log-server")
    register_tools(app, resolved_settings, resolved_auth_provider)
    return app
