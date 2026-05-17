"""System and debugging MCP tools."""

from __future__ import annotations

import logging

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken

from auth.mcp_caller_context import get_request_mcp_caller
from auth.scopes import MCP_HEALTH_READ_SCOPE, MCP_STATUS_READ_SCOPE
from conf import settings
from decorators import workflow_discoverable_tool
from logging_config import get_logger
from utils.types import JSONObject

logger: logging.Logger = get_logger("tools.system")


@workflow_discoverable_tool(MCP_STATUS_READ_SCOPE)
def get_mcp_service_status(
    access_token: AccessToken | None = CurrentAccessToken(),
) -> JSONObject:
    """Return a compact MCP service status payload for diagnostics.

    This tool is mainly intended for development and debugging clients that
    need a quick sanity check of the running server and the authenticated
    caller identity attached by middleware. The returned payload includes:

    - static service identity
    - basic process configuration such as environment, host, port, and log level
    - selected identity fields such as subject, client id, and client type

    It is intentionally lightweight and should not be treated as a full
    operational health or metrics endpoint. Project access is intentionally not
    reported or checked here; middleware already authenticated the caller.
    """

    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "get_mcp_service_status",
        },
    )
    claims = access_token.claims if access_token is not None else {}
    caller = get_request_mcp_caller()
    payload: JSONObject = {
        "name": "mcp-log-server",
        "status": "ok",
        "subject": claims.get("sub", access_token.client_id if access_token is not None else None),
        "client_id": caller.client_id,
        "client_type": caller.client_type,
        "environment": settings.ENVIRONMENT,
        "host": settings.HOST,
        "port": settings.PORT,
        "log_level": settings.LOG_LEVEL,
    }
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "get_mcp_service_status",
            "client_type": payload["client_type"],
            "environment": payload["environment"],
        },
    )
    return payload


@workflow_discoverable_tool(MCP_HEALTH_READ_SCOPE)
def get_mcp_health_check() -> JSONObject:
    """Return the smallest possible MCP health payload.

    This tool exists for callers that only need a binary-style liveness check.
    It intentionally returns a tiny payload with no caller context or runtime
    details, unlike `get_mcp_service_status`.
    """

    logger.info(
        "tool call",
        extra={
            "event": "tool_call",
            "tool_name": "get_mcp_health_check",
        },
    )
    payload: JSONObject = {
        "status": "ok",
    }
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "get_mcp_health_check",
            "status": payload["status"],
        },
    )
    return payload
