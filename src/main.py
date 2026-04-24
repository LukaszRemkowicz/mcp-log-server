"""Process entrypoint for running the local FastMCP HTTP server."""

from __future__ import annotations

import logging

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider

from app import create_application
from auth.auth_provider import build_auth_provider
from logging_config import configure_logging, get_logger
from settings import Settings, get_settings

logger: logging.Logger = get_logger("main")


def main() -> None:
    """Run the FastMCP HTTP service."""

    settings: Settings = get_settings()
    configure_logging(settings)
    auth_provider: AuthProvider | None = build_auth_provider(settings)
    app: FastMCP = create_application(auth_provider=auth_provider)
    logger.info(
        "starting FastMCP HTTP service on %s:%s%s",
        settings.host,
        settings.port,
        settings.mcp_path,
    )
    app.run(
        transport="http",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        path=settings.mcp_path,
        stateless_http=settings.mcp_stateless_http,
        json_response=settings.mcp_json_response,
    )


if __name__ == "__main__":
    main()
