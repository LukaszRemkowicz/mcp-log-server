"""Django-style settings access helpers for the current process."""

from settings import Settings
from settings import get_settings as _get_settings


def get_settings() -> Settings:
    """Return the cached process settings instance."""

    return _get_settings()


settings: Settings = get_settings()
