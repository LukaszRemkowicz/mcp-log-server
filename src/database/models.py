"""Database models for MCP agent call metadata."""

from __future__ import annotations

from typing import Any, ClassVar

from tortoise import fields

from database.managers import DatabaseModel, ObjectsManager


class AgentCall(DatabaseModel):
    """One persisted MCP agent call or move within a session."""

    objects: ClassVar[ObjectsManager[AgentCall]]

    id = fields.UUIDField(
        primary_key=True,
        description="Unique UUID for this recorded MCP call row.",
    )
    created_at = fields.DatetimeField(
        auto_now_add=True,
        description="UTC timestamp when this MCP call row was created.",
    )
    session_id = fields.UUIDField(
        description="MCP-generated UUID shared by all rows that belong to one agent session.",
    )
    session_ended = fields.BooleanField(
        default=False,
        description="Whether this row marks the end of the agent session.",
    )
    workspace = fields.CharField(
        max_length=32,
        description="Agent workspace for the call, currently either 'session' or 'workflow'.",
    )
    event = fields.CharField(
        max_length=128,
        description=(
            "MCP action type, such as mcp_call_tool, mcp_read_resource, "
            "mcp_list_tools, or mcp_call_tool_exception."
        ),
    )
    subject = fields.CharField(
        max_length=255,
        null=True,
        description="Authenticated JWT subject claim for the caller, when available.",
    )
    client_id = fields.CharField(
        max_length=255,
        null=True,
        description="Authenticated FastMCP client id for the caller, when available.",
    )
    client_type = fields.CharField(
        max_length=128,
        null=True,
        description="JWT client_type claim identifying the caller category, when available.",
    )
    tool_name = fields.CharField(
        max_length=255,
        null=True,
        description="MCP tool name when event records a tool call.",
    )
    uri = fields.TextField(
        null=True,
        description=(
            "MCP resource URI when event records a resource read, such as a workflow skill URI."
        ),
    )
    duration_ms = fields.FloatField(
        null=True,
        description="Measured call duration in milliseconds, when timing is available.",
    )
    success = fields.BooleanField(
        default=True,
        description="Whether the call completed successfully from the agent audit perspective.",
    )
    error_code = fields.CharField(
        max_length=128,
        null=True,
        description="Stable error code for failed or rejected calls, when available.",
    )
    project_name = fields.CharField(
        max_length=255,
        null=True,
        description="Project name targeted by the call, when a single project is known.",
    )
    source_keys: fields.Field[list[str] | None] = fields.JSONField(
        null=True,
        description="Manifest source keys requested or affected by the call, when known.",
    )
    arguments: fields.Field[dict[str, Any] | None] = fields.JSONField(
        null=True,
        description="Sanitized MCP call arguments captured for replay or debugging.",
    )
    result_summary: fields.Field[dict[str, Any] | None] = fields.JSONField(
        null=True,
        description="Sanitized compact summary of the MCP call result.",
    )

    class Meta:
        table = "agent_calls"


class ProjectManifest(DatabaseModel):
    """Persist one project manifest with the same shape as manifest JSON."""

    objects: ClassVar[ObjectsManager[ProjectManifest]]

    id = fields.UUIDField(
        primary_key=True,
        description="Unique UUID for this stored manifest row.",
    )
    created_at = fields.DatetimeField(
        auto_now_add=True,
        description="UTC timestamp when this manifest row was created.",
    )
    updated_at = fields.DatetimeField(
        auto_now=True,
        description="UTC timestamp when this manifest row was last updated.",
    )
    project_key = fields.CharField(
        max_length=255,
        unique=True,
        description="Stable project key from the manifest, for example 'landingpage'.",
    )
    project_summary = fields.TextField(
        description="Human-readable project summary from the manifest.",
    )
    static_asset_paths: fields.Field[list[str]] = fields.JSONField(
        default=list,
        description="Static asset paths from the manifest used for noise classification.",
    )
    static_asset_extensions: fields.Field[list[str]] = fields.JSONField(
        default=list,
        description="Static asset file extensions from the manifest used for noise classification.",
    )
    sources: fields.Field[list[dict[str, Any]]] = fields.JSONField(
        description="List of source definitions with the same shape as Manifest.sources.",
    )

    class Meta:
        table = "project_manifests"
