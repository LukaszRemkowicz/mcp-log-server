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

    environment: str = "dev"
    host: str = "127.0.0.1"
    port: int = 8001
    log_level: str = "INFO"
    log_format: str = "text"
    jwt_algorithm: str = "HS256"
    jwt_shared_secret: str = "change-me-local-dev-secret"
    jwt_issuer: str = "mcp-log-server-dev"
    jwt_audience: str = "mcp-log-server"
    jwt_expiration_seconds: int = 86400
    logs_dir: Path = Field(
        default=REPOSITORY_ROOT / "docker-logs",
        alias="DOCKER_LOGS_DIR",
    )
    manifest_path: Path = REPOSITORY_ROOT / "src/manifests/landingpage.json"
    mcp_path: str = "/mcp"
    mcp_stateless_http: bool = True
    mcp_json_response: bool = True

    def model_post_init(self, __context: object) -> None:
        """Normalize relative repository paths after settings load."""

        self.logs_dir = self._resolve_repo_path(self.logs_dir)
        self.manifest_path = self._resolve_repo_path(self.manifest_path)

    @staticmethod
    def _resolve_repo_path(path: Path) -> Path:
        """Resolve relative config paths against the repository root."""

        return path if path.is_absolute() else REPOSITORY_ROOT / path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance for process-wide reuse."""

    return Settings()
