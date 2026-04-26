"""Workflow-discoverable tool registration helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp.server.auth import require_scopes

from app import mcp

_workflow_discoverable_tools_by_name: dict[str, dict[str, str]] = {}


def _get_tool_summary(func: Callable[..., Any]) -> str:
    """Return a short human-readable summary for workflow-discoverable tool metadata."""

    docstring = (func.__doc__ or "").strip()
    if not docstring:
        return func.__name__
    return docstring.splitlines()[0].rstrip(".") + "."


def workflow_discoverable_tool(
    required_scope: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a tool in two places: MCP and the workflow bootstrap registry.

    There are two tool registration styles in this project:

    - `@mcp.tool(...)`
      Register a normal MCP tool only.
    - `@workflow_discoverable_tool(...)`
      Register a normal MCP tool and also add it to the workflow bootstrap
      inventory returned by `analyze_daily_log_bundle`.

    This decorator is just shorthand for:

    1. save lightweight tool metadata in the workflow-discoverable registry
    2. register the function as a normal MCP tool with
       `mcp.tool(auth=require_scopes(required_scope))`

    It does not create a special tool type. The tool is still a normal MCP
    tool. The extra behavior is only that the workflow bootstrap can later
    advertise it in its returned `tools` list.

    For this to work, the module containing the tool must be imported during
    app startup so the decorator actually runs.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        _workflow_discoverable_tools_by_name[func.__name__] = {
            "tool_name": func.__name__,
            "description": _get_tool_summary(func),
            "required_scope": required_scope,
        }
        return mcp.tool(auth=require_scopes(required_scope))(func)

    return decorator


def list_workflow_discoverable_tool_registrations() -> list[dict[str, str]]:
    """Return workflow-discoverable tool registrations in stable registration order."""

    return list(_workflow_discoverable_tools_by_name.values())
