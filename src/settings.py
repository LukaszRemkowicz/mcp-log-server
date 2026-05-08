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
    MANIFEST_PATH: Path = REPOSITORY_ROOT / "src/manifests/projects"
    FILE_SOURCE_ROOT: Path | None = None
    MCP_PATH: str = "/mcp"
    MCP_STATELESS_HTTP: bool = True
    MCP_JSON_RESPONSE: bool = True
    DATABASE_HOST: str = "127.0.0.1"
    DATABASE_PORT: int = 5432
    DATABASE_NAME: str = "mcp_log_server"
    DATABASE_USER: str = "mcp_log_server"
    DATABASE_PASSWORD: str = "mcp-log-server-local-password"

    def model_post_init(self, __context: object) -> None:
        """Normalize relative repository paths after settings load."""

        self.LOGS_DIR = self._resolve_repo_path(self.LOGS_DIR)
        self.MANIFEST_PATH = self._resolve_repo_path(self.MANIFEST_PATH)
        if self.FILE_SOURCE_ROOT is not None:
            self.FILE_SOURCE_ROOT = self._resolve_repo_path(self.FILE_SOURCE_ROOT)

    @property
    def workflow_path(self) -> Path:
        """Return the root path for persisted workflow log snapshots."""

        return self.LOGS_DIR / "workflow"

    def workflow_project_path(self, project_name: str) -> Path:
        """Return the workflow root directory for one project."""

        return self.workflow_path / project_name

    def workflow_snapshot_paths(self, project_name: str) -> tuple[Path, Path]:
        """Return workflow latest and archive paths for one project."""

        workflow_project_path = self.workflow_project_path(project_name)
        latest_output_dir = workflow_project_path / "latest"
        archive_dir = workflow_project_path / "archive"
        return latest_output_dir, archive_dir

    @property
    def session_path(self) -> Path:
        """Return the root path for persisted session log snapshots."""

        return self.LOGS_DIR / "sessions"

    @property
    def manifests_dir(self) -> Path:
        """Return the configured manifests directory."""

        return self.MANIFEST_PATH

    @property
    def file_source_root(self) -> Path:
        """Return the root used for relative file-backed source targets."""

        return self.FILE_SOURCE_ROOT or self.MANIFEST_PATH.parent / "logs"

    @property
    def db(self) -> str:
        """Return the Postgres connection DSN for database clients."""

        username = quote(self.DATABASE_USER, safe="")
        password = quote(self.DATABASE_PASSWORD, safe="")
        database = quote(self.DATABASE_NAME, safe="")
        return (
            f"postgres://{username}:{password}@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{database}"
        )

    @staticmethod
    def _resolve_repo_path(path: Path) -> Path:
        """Resolve relative config paths against the repository root."""

        return path if path.is_absolute() else REPOSITORY_ROOT / path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance for process-wide reuse."""

    return Settings()
