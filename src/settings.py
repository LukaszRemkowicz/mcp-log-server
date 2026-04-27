"""Application settings for the MCP log server."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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
    LOGS_DIR: Path = Field(
        default=REPOSITORY_ROOT / "docker-logs",
        alias="DOCKER_LOGS_DIR",
    )
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
    MANIFEST_PATH: Path = REPOSITORY_ROOT / "src/manifests/landingpage.json"
    MCP_PATH: str = "/mcp"
    MCP_STATELESS_HTTP: bool = True
    MCP_JSON_RESPONSE: bool = True

    def model_post_init(self, __context: object) -> None:
        """Normalize relative repository paths after settings load."""

        self.LOGS_DIR = self._resolve_repo_path(self.LOGS_DIR)
        self.MANIFEST_PATH = self._resolve_repo_path(self.MANIFEST_PATH)

    @staticmethod
    def _resolve_repo_path(path: Path) -> Path:
        """Resolve relative config paths against the repository root."""

        return path if path.is_absolute() else REPOSITORY_ROOT / path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance for process-wide reuse."""

    return Settings()
