"""Middleware that prepares manifests for the authenticated MCP caller."""

from __future__ import annotations

import logging

import mcp.types as mt
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from tortoise.exceptions import BaseORMException

from auth.mcp_authorized_manifests import (
    AUTHORIZED_MANIFESTS_REQUEST_STATE_ATTR,
    AuthorizedProjectManifests,
    freeze_authorized_manifests,
)
from auth.mcp_caller_context import get_request_mcp_caller
from logging_config import get_logger
from manifests.models import Manifest
from services.project_manifest import ProjectManifestService

logger: logging.Logger = get_logger("middleware.authorized_manifests")
manifest_service = ProjectManifestService()


async def _load_authorized_manifests(
    allowed_projects: frozenset[str],
) -> dict[str, Manifest]:
    """Load valid persisted manifests for the caller's authorized projects."""

    manifests: dict[str, Manifest] = {}
    for project_name in sorted(allowed_projects):
        manifest_context = await manifest_service.get(project_name)
        if manifest_context is None:
            continue
        manifests[manifest_context.project_name] = manifest_context.manifest
    return manifests


class AuthorizedManifestsMiddleware(Middleware):
    """Attach DB-backed manifests for the already-authorized MCP caller."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Prepare request.state.authorized_manifests for downstream tools."""

        try:
            request = get_http_request()
        except RuntimeError:
            return await call_next(context)

        caller = get_request_mcp_caller(request)

        try:
            manifests = await _load_authorized_manifests(caller.allowed_projects)
        except BaseORMException:
            logger.exception(
                "failed to load authorized project manifests",
                extra={
                    "event": "authorized_manifests_load_failed",
                    "client_id": caller.client_id,
                    "client_type": caller.client_type,
                    "workspace": caller.workspace,
                },
            )
            manifests = {}

        setattr(
            request.state,
            AUTHORIZED_MANIFESTS_REQUEST_STATE_ATTR,
            AuthorizedProjectManifests(
                caller=caller,
                manifests=freeze_authorized_manifests(manifests),
            ),
        )
        return await call_next(context)
