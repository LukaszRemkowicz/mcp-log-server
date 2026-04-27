"""Workflow-discoverable tool registration helpers."""

from __future__ import annotations

from collections.abc import Callable
from inspect import Parameter, Signature, signature
from typing import Any

from fastmcp.server.auth import require_scopes

from app import mcp

_workflow_discoverable_tools_by_name: dict[str, dict[str, Any]] = {}


def _get_tool_summary(func: Callable[..., Any]) -> str:
    """Return the short summary shown for one workflow-discoverable tool.

    The workflow bootstrap payload should give the agent a concise explanation
    of what each follow-up tool does without copying full docstrings or the
    full FastMCP schema.

    This helper uses only the first non-empty docstring line when available
    and falls back to the function name if the tool has no docstring.
    """

    docstring = (func.__doc__ or "").strip()
    if not docstring:
        return func.__name__
    return docstring.splitlines()[0].rstrip(".") + "."


def _annotation_to_string(annotation: Any) -> str:
    """Normalize Python typing objects into simple agent-facing strings.

    The workflow registry now returns argument metadata for
    `@workflow_discoverable_tool(...)` entries. Once we expose argument
    metadata, each public argument needs a type field too.

    `inspect.signature(...)` gives us raw Python annotation objects such as
    classes, union types, or `typing.*` wrappers. Those are useful inside
    Python, but they are not a good payload format for workflow bootstrap
    metadata because they are:

    - noisy to read
    - awkward to serialize directly
    - more implementation-specific than agents need

    This helper reduces those annotation objects to stable lightweight strings
    like `"str"`, `"int | None"`, or `"list[str]"` so the bootstrap payload
    can describe tool arguments without leaking Python-specific internals.
    """

    if annotation is Signature.empty:
        return "any"
    if isinstance(annotation, str):
        return annotation
    if hasattr(annotation, "__name__"):
        return str(annotation.__name__)
    return str(annotation).replace("typing.", "")


def _is_internal_tool_parameter(parameter: Parameter) -> bool:
    """Return whether one function parameter is runtime wiring, not user input.

    Workflow bootstrap metadata should describe only the arguments an agent is
    expected to supply itself. The registry needs this filter because the raw
    Python function signature includes both:

    - public MCP arguments the caller should send
    - framework-injected parameters the server fills in automatically

    This helper filters out framework/runtime parameters such as:

    - `settings`
    - `access_token`
    - `asset_loader`
    - `Depends(...)`
    - `CurrentAccessToken()`

    Those parameters are real parts of the Python function signature, but they
    are not part of the public MCP contract. If we exposed them in the
    bootstrap inventory, agents would get a misleading callable shape and may
    think they are supposed to provide auth/runtime objects themselves.
    """

    if parameter.kind not in {
        Parameter.POSITIONAL_OR_KEYWORD,
        Parameter.KEYWORD_ONLY,
    }:
        return True

    if parameter.name in {"settings", "access_token", "asset_loader"}:
        return True

    default = parameter.default
    if default is Parameter.empty:
        return False

    default_type = default.__class__
    return (
        default_type.__name__ in {"_Depends", "_CurrentAccessToken"}
        or default_type.__module__.startswith("uncalled_for.")
        or default_type.__module__.startswith("fastmcp.")
    )


def _serialize_default(default: Any) -> Any:
    """Convert one public default value into JSON-friendly metadata.

    The workflow registry stores plain structured data so it can be returned
    directly inside `result.structuredContent`.

    Simple scalar defaults are preserved as-is. More complex runtime objects
    are reduced to `repr(...)` so the registry remains deterministic and easy
    to inspect.
    """

    if default is Signature.empty:
        return None
    if isinstance(default, (str, int, float, bool)) or default is None:
        return default
    if isinstance(default, (list, dict)):
        return default
    return repr(default)


def _get_tool_arguments(
    func: Callable[..., Any],
    *,
    default_overrides: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return the public argument metadata for one workflow-discoverable tool.

    These argument entries are intentionally lighter than FastMCP's full JSON
    schema, but they should still tell the agent:

    - which arguments are public
    - whether each argument is required
    - the rough type for each argument
    - the default value when one exists

    We need this helper because the workflow bootstrap tool returns its own
    lightweight inventory of recommended follow-up tools. Once that inventory
    includes arguments, we have to derive those arguments from the Python
    function signature and strip away framework-only details.

    Internal runtime parameters are filtered out first, then the remaining
    agent-facing arguments are serialized into small registry metadata entries.
    """

    tool_signature = signature(func)
    arguments: list[dict[str, Any]] = []
    for parameter in tool_signature.parameters.values():
        if _is_internal_tool_parameter(parameter):
            continue
        arguments.append(
            {
                "name": parameter.name,
                "type": _annotation_to_string(parameter.annotation),
                "required": parameter.default is Signature.empty,
                "default": _serialize_default(
                    default_overrides.get(parameter.name, parameter.default)
                    if default_overrides is not None
                    else parameter.default
                ),
            }
        )
    return arguments


def workflow_discoverable_tool(
    required_scope: str,
    *,
    argument_default_overrides: dict[str, Any] | None = None,
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

    The saved registry metadata currently includes:

    - tool name
    - short description
    - required scope
    - public argument metadata

    That argument metadata is intentionally smaller than the normal MCP
    `tools/list` schema, but it gives the workflow agent enough detail to see
    the callable shape of recommended follow-up tools without needing a second
    discovery call first.

    For this to work, the module containing the tool must be imported during
    app startup so the decorator actually runs.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        _workflow_discoverable_tools_by_name[func.__name__] = {
            "tool_name": func.__name__,
            "description": _get_tool_summary(func),
            "required_scope": required_scope,
            "arguments": _get_tool_arguments(
                func,
                default_overrides=argument_default_overrides,
            ),
        }
        return mcp.tool(auth=require_scopes(required_scope))(func)

    return decorator


def list_workflow_discoverable_tool_registrations() -> list[dict[str, Any]]:
    """Return the stored workflow bootstrap tool catalog in registration order.

    This helper does not perform filtering itself. `tools.workflow` applies JWT
    scope filtering later when building the bootstrap payload.

    Stable registration order keeps the returned workflow tool inventory
    predictable in tests and easier to reason about when new tools are added.
    """

    return list(_workflow_discoverable_tools_by_name.values())
