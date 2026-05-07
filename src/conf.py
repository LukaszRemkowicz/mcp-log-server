"""Django-style settings access helpers for the current process."""

from typing import Any, cast

from settings import Settings
from settings import get_settings as _get_settings


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


_settings_proxy = SettingsProxy(_get_settings())


def get_settings() -> Settings:
    """Return the concrete settings object currently used by the proxy."""

    return _settings_proxy.get_wrapped()


def set_settings(settings: Settings) -> None:
    """Replace the concrete settings object used by the process-wide proxy."""

    _settings_proxy.set_wrapped(settings)


settings: Settings = cast(Settings, _settings_proxy)
