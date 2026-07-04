"""Runtime settings for the socket app."""

from __future__ import annotations

from pathlib import Path

import environ

env = environ.Env()

DOCKER_TIMEOUT_SECONDS = 15
MAX_LOG_BYTES = 1_000_000
MAX_FILE_BYTES = 200_000
MAX_DIRECTORY_ENTRIES = 200
MAX_VPS_CONTAINERS = 200
MAX_VPS_VOLUMES = 200
MAX_TRAEFIK_ROUTERS = 200
MAX_CROWDSEC_OUTPUT_CHARS = 20000
SOCKET_APP_SOCKET_PATH = Path(
    env.str("SOCKET_APP_SOCKET_PATH", default="/run/socket-app/gateway.sock")
)
TRAEFIK_ROUTER_LABEL_PREFIX = "traefik.http.routers."
TRAEFIK_ROUTER_SAFE_PROPERTIES = frozenset(
    {"entrypoints", "rule", "service", "tls", "tls.certresolver"}
)
ANONYMOUS_VOLUME_NAME_PATTERN = r"^[0-9a-f]{64}$"
SAFE_COMPOSE_LABEL_KEYS = frozenset(
    {
        "com.docker.compose.project",
        "com.docker.compose.service",
        "com.docker.compose.container-number",
        "com.docker.compose.oneoff",
        "com.docker.compose.volume",
    }
)
SAFE_ENV_VALUE_NAMES = frozenset(
    {
        "APP_ENV",
        "DATABASE_HOST",
        "DATABASE_NAME",
        "DATABASE_PORT",
        "DATABASE_USER",
        "DB_HOST",
        "DB_NAME",
        "DB_PORT",
        "DB_USER",
        "ENV",
        "ENVIRONMENT",
        "LOG_LEVEL",
        "NODE_ENV",
        "PORT",
        "POSTGRES_DB",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
    }
)
SECRET_ENV_NAME_PARTS = frozenset(
    {
        "ACCESS_KEY",
        "API_KEY",
        "AUTH",
        "BROKER_URL",
        "CREDENTIAL",
        "DATABASE_URL",
        "DB_URL",
        "DSN",
        "KEY_FILE",
        "PASSWORD",
        "PRIVATE_KEY",
        "REDIS_URL",
        "SECRET",
        "TOKEN",
    }
)
