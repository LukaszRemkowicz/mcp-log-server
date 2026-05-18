"""Resolve the configured caller authorization model."""

from __future__ import annotations

from functools import lru_cache
from importlib import import_module

from conf import settings
from database.models import McpCaller


@lru_cache(maxsize=1)
def get_mcp_caller_model() -> type[McpCaller]:
    """Return the configured MCP caller model class.

    This mirrors Django's `get_user_model` pattern so authorization services do
    not need to import the concrete model directly.
    """

    module_path, _, class_name = settings.CALLER_AUTH.rpartition(".")
    if not module_path or not class_name:
        msg = "CALLER_AUTH must be a dotted Python path."
        raise RuntimeError(msg)

    model = getattr(import_module(module_path), class_name)
    if not isinstance(model, type) or not issubclass(model, McpCaller):
        msg = "CALLER_AUTH must point to a McpCaller model class."
        raise RuntimeError(msg)
    return model
