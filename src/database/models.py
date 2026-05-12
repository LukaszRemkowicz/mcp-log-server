"""Database models for MCP agent call metadata."""

from __future__ import annotations

from typing import Any, ClassVar

from tortoise import fields

from conf import settings
from database.fields import FileField, FileStorage
from database.managers import CollectLogsManager, DatabaseModel, ObjectsManager
from database.types import (
    AgentCallEvent,
    CollectLogsSourceStatus,
    LogSourceType,
    LogStream,
    LogWorkspace,
)


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
        description=(
            "Whether this row marks the end of the agent session. Planned "
            "close_agent_session support will write this explicitly."
        ),
    )
    workspace = fields.CharEnumField(
        LogWorkspace,
        description=(
            "Requested log workspace for the call: 'workflow' for shared scheduled "
            "workflow context or 'session' for an interactive investigation."
        ),
    )
    event = fields.CharEnumField(
        AgentCallEvent,
        description=(
            "MCP action type, such as mcp_call_tool, mcp_read_resource, "
            "mcp_list_tools, or mcp_call_tool_exception."
        ),
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
            "MCP resource URI when event records a resource read, such as a workflow "
            "skill URI. Tool-call rows leave this empty."
        ),
    )
    duration_seconds = fields.FloatField(
        null=True,
        description="Measured call duration in seconds, when timing is available.",
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

    class Meta:
        table = "agent_calls"


class CollectLogs(DatabaseModel):
    """Persist metadata for one collect_logs artifact."""

    objects: ClassVar[CollectLogsManager[CollectLogs]] = CollectLogsManager()

    id = fields.BigIntField(
        primary_key=True,
        description="Database-generated integer id for this collected log artifact.",
    )
    created_at = fields.DatetimeField(
        auto_now_add=True,
        description="UTC timestamp when this collected log metadata row was created.",
    )
    session_id = fields.UUIDField(
        null=True,
        description="Agent session UUID for session workspace collections.",
    )
    workspace = fields.CharEnumField(
        LogWorkspace,
        description="Collection workspace, currently 'workflow' or 'session'.",
    )
    project_name = fields.CharField(
        max_length=255,
        description="Manifest project key collected by this artifact.",
    )
    collected_at = fields.DatetimeField(
        description="UTC timestamp when collection metadata was produced.",
    )
    snapshot_dir = fields.TextField(
        description="Persisted snapshot directory path under the logs root.",
    )
    metadata_file = FileField(
        max_length=1024,
        description="Path to snapshot_metadata.json or workflow_inventory.json.",
    )
    archive_name = fields.CharField(
        max_length=255,
        null=True,
        description="Workflow archive name when this workflow artifact is archived.",
    )
    is_latest = fields.BooleanField(
        default=False,
        description="Whether this is the latest workflow artifact for the project.",
    )
    requested_source_keys: fields.Field[list[str]] = fields.JSONField(
        default=list,
        description="Source keys requested by the caller before manifest resolution.",
    )
    resolved_source_keys: fields.Field[list[str]] = fields.JSONField(
        default=list,
        description="Source keys resolved from the manifest and attempted for collection.",
    )
    unknown_requested_source_keys: fields.Field[list[str]] = fields.JSONField(
        default=list,
        description="Requested source keys that were not present in the manifest.",
    )
    requested_since = fields.CharField(
        max_length=255,
        null=True,
        description="Original collect_logs since argument.",
    )
    requested_until = fields.CharField(
        max_length=255,
        null=True,
        description="Original collect_logs until argument.",
    )
    warnings: fields.Field[list[str]] = fields.JSONField(
        default=list,
        description="Deterministic warnings returned for this project collection.",
    )
    retry_tips: fields.Field[list[str]] = fields.JSONField(
        default=list,
        description="Retry guidance returned for this project collection.",
    )

    class Meta:
        table = "collect_logs"


class CollectLogsSource(DatabaseModel):
    """Persist metadata for one source file inside a collect_logs artifact."""

    objects: ClassVar[ObjectsManager[CollectLogsSource]]

    id = fields.BigIntField(
        primary_key=True,
        description="Database-generated integer id for this collected source metadata row.",
    )
    created_at = fields.DatetimeField(
        auto_now_add=True,
        description="UTC timestamp when this collected source metadata row was created.",
    )
    collect_logs: fields.ForeignKeyRelation[CollectLogs] = fields.ForeignKeyField(
        "models.CollectLogs",
        related_name="sources",
        description="Parent collect_logs artifact this source file belongs to.",
    )
    source_key = fields.CharField(
        max_length=255,
        description="Manifest source key, for example backend, nginx, or frontend.",
    )
    source_type = fields.CharEnumField(
        LogSourceType,
        description="Manifest source type, currently docker or file.",
    )
    target = fields.TextField(
        description="Manifest target used for collection, such as container name or file path.",
    )
    description = fields.TextField(
        description="Human-readable manifest source description.",
    )
    stream = fields.CharEnumField(
        LogStream,
        null=True,
        description="Requested stream metadata, such as stdout or stderr.",
    )
    parser_type = fields.CharField(
        max_length=128,
        null=True,
        description="Parser profile from manifest metadata.",
    )
    normalization_profile = fields.CharField(
        max_length=128,
        null=True,
        description="Normalization profile used by deterministic analysis tools.",
    )
    default_noise_profile = fields.CharField(
        max_length=128,
        null=True,
        description="Default noise profile used by filtering tools.",
    )
    status = fields.CharEnumField(
        CollectLogsSourceStatus,
        description="Collection status, currently collected or unavailable.",
    )
    file = FileField(
        storage=FileStorage(location=settings.LOGS_DIR),
        max_length=1024,
        null=True,
        description=(
            "Logs-root-relative source file path, for example "
            "sessions/<session_id>/<project_name>/<source>.log or "
            "workflow/<project_name>/latest/<source>.log."
        ),
    )
    line_count = fields.IntField(
        default=0,
        description="Number of lines persisted for this source.",
    )
    error = fields.TextField(
        null=True,
        description="Source-level collection error when status is unavailable.",
    )
    retry_tips: fields.Field[list[str]] = fields.JSONField(
        default=list,
        description="Source-level retry guidance when collection failed.",
    )

    class Meta:
        table = "collect_logs_sources"


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
