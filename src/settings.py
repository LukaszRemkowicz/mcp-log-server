"""Application settings for the MCP log server."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        extra="ignore",
    )

    environment: str = "dev"
    host: str = "127.0.0.1"
    port: int = 8001
    log_level: str = "INFO"
    manifest_path: Path = Path("manifests/landingpage.json")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance for process-wide reuse."""

    return Settings()
