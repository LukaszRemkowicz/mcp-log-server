"""Application settings for the MCP log server."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Process configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        extra="ignore",
        populate_by_name=True,
    )

    ENVIRONMENT: str = "dev"
    HOST: str = "127.0.0.1"
    PORT: int = 8001
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"
    JWT_ALGORITHM: str = "HS256"
    JWT_SHARED_SECRET: str = "change-me-local-dev-secret"
    JWT_ISSUER: str = "mcp-log-server-dev"
    JWT_AUDIENCE: str = "mcp-log-server"
    JWT_EXPIRATION_SECONDS: int = 86400
    LOGS_DIR: Path = REPOSITORY_ROOT / "logs"
    DEFAULT_LOG_WINDOW: str = Field(
        default="24h",
        alias="DEFAULT_LOG_WINDOW",
    )
    WORKFLOW_ARCHIVE_RETENTION: str = Field(
        default="14d",
        alias="WORKFLOW_ARCHIVE_RETENTION",
    )
    LOG_SNAPSHOT_RETENTION: str = Field(
        default="7d",
        alias="LOG_SNAPSHOT_RETENTION",
    )
    MCP_PATH: str = "/mcp"
    MCP_STATELESS_HTTP: bool = True
    MCP_JSON_RESPONSE: bool = True
    MCP_CALLER_MODEL: str = "database.models.McpCaller"
    DATABASE_HOST: str = "127.0.0.1"
    DATABASE_PORT: int = 5432
    DATABASE_NAME: str = "mcp_log_server"
    DATABASE_USER: str = "mcp_log_server"
    DATABASE_PASSWORD: str = "mcp-log-server-local-password"

    @property
    def db(self) -> str:
        """Return the Postgres connection DSN for database clients."""

        username = quote(self.DATABASE_USER, safe="")
        password = quote(self.DATABASE_PASSWORD, safe="")
        database = quote(self.DATABASE_NAME, safe="")
        return (
            f"postgres://{username}:{password}@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{database}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance for process-wide reuse."""

    return Settings()
