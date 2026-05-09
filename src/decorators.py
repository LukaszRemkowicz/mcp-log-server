"""Core MCP decorators used across the project."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from enum import StrEnum
from functools import wraps
from inspect import Parameter, Signature, iscoroutinefunction, signature
from typing import Any

from fastmcp.server.auth import AccessToken, require_scopes
from fastmcp.server.dependencies import get_access_token

from app import mcp
from logging_config import get_logger
from services.project_authorization import ProjectAuthorizationError, ProjectAuthorizationService
from services.project_manifest import ProjectManifestService
from utils.mcp_errors import build_agent_tool_error_result
from utils.types import JSONObject, JSONValue

logger = get_logger("decorators")
project_authorization_service = ProjectAuthorizationService()
project_manifest_service = ProjectManifestService()
_workflow_discoverable_tools_by_name: dict[str, dict[str, Any]] = {}


class ProjectArgumentName(StrEnum):
    PROJECT_NAME = "project_name"
    PROJECT_NAMES = "project_names"


def _get_tool_summary(func: Callable[..., Any]) -> str:
    """Return the short summary shown for one workflow-discoverable tool."""

    docstring = (func.__doc__ or "").strip()
    if not docstring:
        return func.__name__
    return docstring.splitlines()[0].rstrip(".") + "."


def _annotation_to_string(annotation: Any) -> str:
    """Normalize Python typing objects into simple agent-facing strings."""

    if annotation is Signature.empty:
        return "any"
    if isinstance(annotation, str):
        return annotation
    if hasattr(annotation, "__name__"):
        return str(annotation.__name__)
    return str(annotation).replace("typing.", "")


def _is_internal_tool_parameter(parameter: Parameter) -> bool:
    """Return whether one function parameter is runtime wiring, not user input."""

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
    """Convert one public default value into JSON-friendly metadata."""

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
    """Return the public argument metadata for one workflow-discoverable tool."""

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
    mcp_description: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register one MCP tool and expose it in workflow bootstrap metadata."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        _workflow_discoverable_tools_by_name[func.__name__] = {
            "tool_name": func.__name__,
            "description": mcp_description or _get_tool_summary(func),
            "required_scope": required_scope,
            "arguments": _get_tool_arguments(
                func,
                default_overrides=argument_default_overrides,
            ),
        }
        return mcp.tool(
            auth=require_scopes(required_scope),
            description=mcp_description,
        )(func)

    return decorator


def list_workflow_discoverable_tool_registrations() -> list[dict[str, Any]]:
    """Return the stored workflow bootstrap tool catalog in registration order."""

    return list(_workflow_discoverable_tools_by_name.values())


def async_[**P, T](func: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, T]:
    """Wrap an async Typer command so Click can execute it synchronously."""

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        return asyncio.run(func(*args, **kwargs))

    return wrapper


def _get_bound_access_token(bound_arguments: dict[str, Any]) -> AccessToken | None:
    """Return the authenticated MCP token, falling back to direct test calls."""

    access_token = get_access_token()
    if isinstance(access_token, AccessToken):
        return access_token
    explicit_access_token = bound_arguments.get("access_token")
    return explicit_access_token if isinstance(explicit_access_token, AccessToken) else None


def _project_authorization_retry_tips(
    project_argument_name: ProjectArgumentName,
    retry_tips: list[str],
) -> list[str]:
    """Return retry guidance using the caller-facing project argument name."""

    if project_argument_name == ProjectArgumentName.PROJECT_NAME:
        return retry_tips
    return [
        "Retry with project_names allowed by the current JWT project access rules.",
        "Use get_mcp_service_status to confirm the current project access before retrying.",
    ]


def _project_authorization_details(
    project_argument_name: ProjectArgumentName,
    requested_project: Any,
) -> JSONObject:
    """Return JSON-safe error details for one rejected project authorization request."""

    requested_value: JSONValue
    if isinstance(requested_project, str) or requested_project is None:
        requested_value = requested_project
    elif isinstance(requested_project, list):
        requested_value = [
            project_name for project_name in requested_project if isinstance(project_name, str)
        ]
    else:
        requested_value = None

    return {project_argument_name.value: requested_value}


def project_authorized_tool(
    func: Callable[..., Any],
) -> Callable[..., Any]:
    """Authorize project argument values before executing one MCP tool.

    Supports the two project argument conventions used by MCP tools:
    `project_name` for one project and `project_names` for many projects.
    """

    func_signature = signature(func)

    async def _authorize_and_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        bargs = func_signature.bind_partial(*args, **kwargs)
        access_token = _get_bound_access_token(bargs.arguments)
        project_argument_name = (
            ProjectArgumentName.PROJECT_NAMES
            if ProjectArgumentName.PROJECT_NAMES in bargs.arguments
            else ProjectArgumentName.PROJECT_NAME
        )
        requested_project = bargs.arguments.get(project_argument_name)
        if not isinstance(access_token, AccessToken):
            logger.info(
                "project authorization decorator rejected missing token",
                extra={
                    "event": "project_authorized_tool_missing_token",
                    "tool_name": func.__name__,
                    "project_argument_name": project_argument_name.value,
                    "requested_project": requested_project,
                },
            )
            return build_agent_tool_error_result(
                error_code="missing_access_token",
                message="access_token is required for project authorization.",
                retry_tips=["Retry through the authenticated MCP HTTP path."],
                details=_project_authorization_details(
                    project_argument_name,
                    requested_project,
                ),
            )

        authorized_project: str | list[str] | ProjectAuthorizationError
        if project_argument_name == ProjectArgumentName.PROJECT_NAMES:
            available_project_names = [
                project_summary.project_name
                for project_summary in (await project_manifest_service.all()).root
            ]
            authorized_project = project_authorization_service.authorize_caller_for_projects(
                access_token,
                requested_project_names=(
                    requested_project if isinstance(requested_project, list) else None
                ),
                available_project_names=available_project_names,
            )
        else:
            authorized_project = project_authorization_service.authorize_caller_for_project(
                access_token,
                requested_project if isinstance(requested_project, str) else None,
            )

        if isinstance(authorized_project, ProjectAuthorizationError):
            logger.info(
                "project authorization decorator rejected project access",
                extra={
                    "event": "project_authorized_tool_rejected",
                    "tool_name": func.__name__,
                    "project_argument_name": project_argument_name.value,
                    "requested_project": requested_project,
                    "error_code": authorized_project.error_code,
                },
            )
            return build_agent_tool_error_result(
                error_code=authorized_project.error_code,
                message=authorized_project.message,
                retry_tips=_project_authorization_retry_tips(
                    project_argument_name,
                    authorized_project.retry_tips,
                ),
                details=_project_authorization_details(
                    project_argument_name,
                    requested_project,
                ),
            )

        bargs.arguments[project_argument_name] = authorized_project
        logger.info(
            "project authorization decorator accepted project access",
            extra={
                "event": "project_authorized_tool_authorized",
                "tool_name": func.__name__,
                "project_argument_name": project_argument_name.value,
                "requested_project": requested_project,
                "authorized_project": authorized_project,
            },
        )
        if iscoroutinefunction(func):
            return await func(*bargs.args, **bargs.kwargs)
        return func(*bargs.args, **bargs.kwargs)

    @wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        return await _authorize_and_call(args, kwargs)

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(_authorize_and_call(args, kwargs))

    return async_wrapper if iscoroutinefunction(func) else wrapper
