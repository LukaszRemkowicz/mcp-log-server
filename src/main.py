"""Process entrypoint for running the local FastMCP HTTP server."""

from __future__ import annotations

import logging

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider

from app import create_application
from auth.auth_provider import build_auth_provider
from conf import settings
from logging_config import configure_logging, get_logger

logger: logging.Logger = get_logger("main")


def main() -> None:
    """Run the FastMCP HTTP service."""

    configure_logging(settings)
    auth_provider: AuthProvider | None = build_auth_provider(settings)
    app: FastMCP = create_application(auth_provider=auth_provider)
    logger.info(
        "starting FastMCP HTTP service on %s:%s%s",
        settings.HOST,
        settings.PORT,
        settings.MCP_PATH,
    )
    app.run(
        transport="http",
        host=settings.HOST,
        port=settings.PORT,
        log_level=settings.LOG_LEVEL,
        path=settings.MCP_PATH,
        stateless_http=settings.MCP_STATELESS_HTTP,
        json_response=settings.MCP_JSON_RESPONSE,
    )


if __name__ == "__main__":
    main()
