"""Django-style settings for the MCP log server."""

from __future__ import annotations

import os
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MCP_PORT", "8001"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_FORMAT = "json"
JWT_ALGORITHM = "HS256"
JWT_SHARED_SECRET = os.environ.get("JWT_SHARED_SECRET", "change-me-local-dev-secret")
JWT_ISSUER = os.environ.get("JWT_ISSUER", "mcp-log-server-dev")
JWT_AUDIENCE = os.environ.get("JWT_AUDIENCE", "mcp-log-server")
JWT_EXPIRATION_SECONDS = 86400
LOGS_DIR = Path(os.environ.get("LOGS_DIR", (REPOSITORY_ROOT / "logs").as_posix()))
DEFAULT_LOG_WINDOW = "24h"
WORKFLOW_ARCHIVE_RETENTION = "14d"
LOG_SNAPSHOT_RETENTION = "7d"
FAIL2BAN_SOCKET_PATH = Path(
    os.environ.get("FAIL2BAN_SOCKET_PATH", "/var/run/fail2ban/fail2ban.sock")
)
FAIL2BAN_CLIENT_COMMAND = "fail2ban-client"
FAIL2BAN_JAILS = ["portfolio-nginx-probes", "portfolio-traefik-probes"]
FAIL2BAN_COMMAND_TIMEOUT_SECONDS = 5
FAIL2BAN_PROXY_URL = os.environ.get("FAIL2BAN_PROXY_URL", "").rstrip("/")
MCP_PATH = "/mcp"
MCP_STATELESS_HTTP = True
MCP_JSON_RESPONSE = True
CALLER_AUTH = "database.models.McpCaller"
DATABASE_HOST = os.environ.get("DATABASE_HOST", "127.0.0.1")
DATABASE_PORT = int(os.environ.get("DATABASE_PORT", "5432"))
DATABASE_NAME = os.environ.get("DATABASE_NAME", "mcp_log_server")
DATABASE_USER = os.environ.get("DATABASE_USER", "mcp_log_server")
DATABASE_PASSWORD = os.environ.get("DATABASE_PASSWORD", "mcp-log-server-local-password")
