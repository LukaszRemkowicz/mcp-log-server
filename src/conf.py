"""Django-style settings access helpers for the current process.

Settings are intentionally loaded from uppercase values in `settings.py`
instead of a Pydantic settings model. The Pydantic approach made each new
setting live in two places: once as the real value and again as a model field.
Keeping constants out of environment loading also required a separate
`ClassVar` path, which made ordinary `override_settings()` behavior awkward
because Pydantic model copies only update model fields, not class variables.

This module keeps `settings.py` as the single source of truth while still
providing a stable `conf.settings` proxy. Test/runtime overrides stay plain:
copy the current settings object, replace attributes, and restore the previous
object afterward.
"""

from collections.abc import Mapping
from copy import copy
from functools import lru_cache
from types import ModuleType
from typing import Any, cast
from urllib.parse import quote

import settings as settings_module

SettingsSource = ModuleType | Mapping[str, Any]
PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production"})
INSECURE_PRODUCTION_VALUES = frozenset(
    {
        "",
        "change-me-local-dev-secret",
        "local-secret",
        "mcp-log-server-local-password",
    }
)


class Settings:
    """Mutable settings object loaded from uppercase module values."""

    def __init__(self, *sources: SettingsSource, **overrides: Any) -> None:
        """Load uppercase settings from source modules/mappings plus overrides."""

        for source in sources or (settings_module,):
            for name, value in _source_settings(source).items():
                setattr(self, name, copy(value))
        for name, value in overrides.items():
            setattr(self, name, copy(value))

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)

    @property
    def db(self) -> str:
        """Return the Postgres connection DSN for database clients."""

        username = quote(self.DATABASE_USER, safe="")
        password = quote(self.DATABASE_PASSWORD, safe="")
        database = quote(self.DATABASE_NAME, safe="")
        return (
            f"postgres://{username}:{password}@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{database}"
        )

    def copy(self, **updates: Any) -> "Settings":
        """Return a shallow settings copy with optional overrides."""

        values = vars(self).copy()
        values.update(updates)
        return Settings(**values)


def _source_settings(source: SettingsSource) -> dict[str, Any]:
    """Return uppercase settings from one module or mapping source."""

    values = vars(source) if isinstance(source, ModuleType) else source
    return {name: value for name, value in values.items() if name.isupper()}


class SettingsProxy:
    """Stable settings object that forwards attribute access to wrapped settings."""

    def __init__(self, wrapped: Settings) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def get_wrapped(self) -> Settings:
        """Return the concrete settings object currently used by the proxy."""

        return self._wrapped

    def set_wrapped(self, wrapped: Settings) -> None:
        """Replace the concrete settings object used by the proxy."""

        self._wrapped = wrapped


@lru_cache(maxsize=1)
def _get_settings() -> Settings:
    """Return a cached settings instance for process-wide reuse."""

    return Settings()


_settings_proxy = SettingsProxy(_get_settings())


def get_settings() -> Settings:
    """Return the concrete settings object currently used by the proxy."""

    return _settings_proxy.get_wrapped()


def set_settings(settings: Settings) -> None:
    """Replace the concrete settings object used by the process-wide proxy."""

    _settings_proxy.set_wrapped(settings)


def validate_runtime_settings(runtime_settings: Settings) -> None:
    """Reject unsafe production runtime settings before the server starts."""

    environment = str(runtime_settings.ENVIRONMENT).lower()
    if environment not in PRODUCTION_ENVIRONMENTS:
        return

    required_production_secrets = {
        "JWT_SHARED_SECRET": runtime_settings.JWT_SHARED_SECRET,
        "DATABASE_PASSWORD": runtime_settings.DATABASE_PASSWORD,
    }
    for name, value in required_production_secrets.items():
        normalized_value = str(value).strip()
        if normalized_value in INSECURE_PRODUCTION_VALUES:
            raise RuntimeError(f"{name} must be set to a production secret.")


settings: Settings = cast(Settings, _settings_proxy)
