"""Django-style settings for the MCP log server."""

from __future__ import annotations

from pathlib import Path

import environ  # type: ignore[import-untyped]

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
env = environ.Env()

env_file = REPOSITORY_ROOT / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)


ENVIRONMENT = env.str("ENVIRONMENT", default="dev")
MCP_HOST = env.str("MCP_HOST", default="127.0.0.1")
MCP_PORT = env.int("MCP_PORT", default=8001)
LOG_LEVEL = env.str("LOG_LEVEL", default="INFO")
LOG_FORMAT = "json"
JWT_ALGORITHM = "HS256"
JWT_SHARED_SECRET = env.str("JWT_SHARED_SECRET", default="change-me-local-dev-secret")
JWT_JWKS_URI = env.str("JWT_JWKS_URI", default="")
JWT_ISSUER = env.str("JWT_ISSUER", default="mcp-log-server-dev")
JWT_AUDIENCE = env.str("JWT_AUDIENCE", default="mcp-log-server")
JWT_EXPIRATION_SECONDS = 86400
LOGS_DIR = Path(env.str("LOGS_DIR", default=(REPOSITORY_ROOT / "logs").as_posix()))
PROJECT_MANIFESTS_PATH = Path(
    env.str(
        "PROJECT_MANIFESTS_PATH",
        default=(REPOSITORY_ROOT / "src/manifests/projects").as_posix(),
    )
)
DEFAULT_LOG_WINDOW = "24h"
WORKFLOW_ARCHIVE_RETENTION = "14d"
LOG_SNAPSHOT_RETENTION = "7d"
SOCKET_APP_SOCKET_PATH = Path(
    env.str("SOCKET_APP_SOCKET_PATH", default="/run/socket-app/gateway.sock")
)
DOCKER_SOCKET_APP_TIMEOUT_SECONDS = env.int(
    "DOCKER_SOCKET_APP_TIMEOUT_SECONDS",
    default=60,
)
SITE_DOMAIN = env.str("SITE_DOMAIN", default="localhost").strip()
TLS_CERTIFICATE_SUBDOMAINS = env.list(
    "TLS_CERTIFICATE_SUBDOMAINS",
    default=["admin", "stage", "mcp"],
)
TLS_CERTIFICATE_TIMEOUT_SECONDS = env.int("TLS_CERTIFICATE_TIMEOUT_SECONDS", default=5)
TLS_CERTIFICATE_EXPIRY_WARNING_DAYS = env.int("TLS_CERTIFICATE_EXPIRY_WARNING_DAYS", default=30)
SCHEDULER_INSPECTION_ROOTS = [
    Path(path)
    for path in env.list(
        "SCHEDULER_INSPECTION_ROOTS",
        default=[
            "/host/etc/cron.d",
            "/host/etc/cron.daily",
            "/host/etc/cron.weekly",
            "/host/var/spool/cron",
            "/host/etc/systemd/system",
        ],
    )
]
MCP_PATH = "/mcp"
MCP_STATELESS_HTTP = True
MCP_JSON_RESPONSE = True
CALLER_AUTH = "database.models.McpCaller"
DATABASE_HOST = env.str("DATABASE_HOST", default="127.0.0.1")
DATABASE_PORT_HOST = env.int("DATABASE_PORT_HOST", default=5437)
DATABASE_PORT = env.int("DATABASE_PORT", default=DATABASE_PORT_HOST)
DATABASE_NAME = env.str("DATABASE_NAME", default="mcp_log_server")
DATABASE_USER = env.str("DATABASE_USER", default="mcp_log_server")
DATABASE_PASSWORD = env.str("DATABASE_PASSWORD", default="mcp-log-server-local-password")
