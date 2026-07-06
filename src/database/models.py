"""Database models for MCP agent call metadata."""

from __future__ import annotations

from typing import Any, ClassVar

from tortoise import fields

from cache import clear_cache_namespace
from core.types import LogWorkspace
from database.fields import FileField, FileStorage
from database.managers import CollectLogsManager, DatabaseModel, ObjectsManager
from database.types import (
    AgentCallEvent,
    AgentSessionStatus,
    CollectLogsSourceStatus,
    LogSourceType,
    LogStream,
    TaskStatus,
    TaskType,
)
from storage import storage as log_storage


class McpCaller(DatabaseModel):
    """Manually managed MCP caller allowlist entry."""

    objects: ClassVar[ObjectsManager[McpCaller]]

    id = fields.BigIntField(
        primary_key=True,
        description="Database-generated integer id for this allowed MCP caller.",
    )
    created_at = fields.DatetimeField(
        auto_now_add=True,
        description="UTC timestamp when this allowed MCP caller row was created.",
    )
    updated_at = fields.DatetimeField(
        auto_now=True,
        description="UTC timestamp when this allowed MCP caller row was last updated.",
    )
    client_id = fields.CharField(
        max_length=255,
        description="Stable client_id claim allowed to call MCP tools.",
    )
    client_type = fields.CharField(
        max_length=128,
        description="Stable client_type claim allowed for this MCP client id.",
    )
    workspace = fields.CharEnumField(
        LogWorkspace,
        description="MCP workspace this caller is allowed to use.",
    )
    allowed_projects: fields.Field[list[str]] = fields.JSONField(
        default=list,
        description="Project names this MCP caller row is allowed to access.",
    )

    class Meta:
        table = "mcp_callers"
        unique_together = (("client_id", "client_type", "workspace"),)


class AgentSession(DatabaseModel):
    """One agent session owned by one MCP caller."""

    objects: ClassVar[ObjectsManager[AgentSession]]

    id = fields.BigIntField(
        primary_key=True,
        description="Database-generated integer id for this agent session.",
    )
    created_at = fields.DatetimeField(
        auto_now_add=True,
        description="UTC timestamp when this agent session was created.",
    )
    updated_at = fields.DatetimeField(
        auto_now=True,
        description="UTC timestamp when this agent session was last updated.",
    )
    name = fields.CharField(
        max_length=24,
        unique=True,
        description="Human-readable session name returned to agents as session_id.",
    )
    caller: fields.ForeignKeyRelation[McpCaller] = fields.ForeignKeyField(
        "models.McpCaller",
        related_name="agent_sessions",
        on_delete=fields.CASCADE,
        description="Allowed MCP caller that owns this agent session.",
    )
    status = fields.CharEnumField(
        AgentSessionStatus,
        default=AgentSessionStatus.ACTIVE,
        description="Lifecycle status for this agent session.",
    )
    closed_at = fields.DatetimeField(
        null=True,
        description="UTC timestamp when this session was closed, if closed.",
    )

    class Meta:
        table = "agent_sessions"


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
    session: fields.ForeignKeyRelation[AgentSession] = fields.ForeignKeyField(
        "models.AgentSession",
        related_name="agent_calls",
        on_delete=fields.CASCADE,
        description="Session that owns this recorded MCP call row.",
    )
    caller: fields.ForeignKeyRelation[McpCaller] = fields.ForeignKeyField(
        "models.McpCaller",
        related_name="agent_calls",
        on_delete=fields.CASCADE,
        description="Allowed MCP caller that created this recorded MCP call row.",
    )
    event = fields.CharEnumField(
        AgentCallEvent,
        description=(
            "MCP action type, such as mcp_call_tool, mcp_read_resource, "
            "mcp_list_tools, or mcp_call_tool_exception."
        ),
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


class Task(DatabaseModel):
    """One persisted async MCP task, such as a background log collection."""

    objects: ClassVar[ObjectsManager[Task]]

    id = fields.UUIDField(
        primary_key=True,
        description="Stable task id returned to MCP clients for polling.",
    )
    created_at = fields.DatetimeField(
        auto_now_add=True,
        description="UTC timestamp when this async task row was created.",
    )
    updated_at = fields.DatetimeField(
        auto_now=True,
        description="UTC timestamp when this async task row was last updated.",
    )
    task_type = fields.CharEnumField(
        TaskType,
        description="Async task kind, for example log_collection.",
    )
    status = fields.CharEnumField(
        TaskStatus,
        default=TaskStatus.QUEUED,
        description="Async task lifecycle status.",
    )
    workspace = fields.CharEnumField(
        LogWorkspace,
        description="MCP workspace this task runs within.",
    )
    caller: fields.ForeignKeyRelation[McpCaller] = fields.ForeignKeyField(
        "models.McpCaller",
        related_name="tasks",
        on_delete=fields.CASCADE,
        description="Allowed MCP caller that created this async task.",
    )
    session_id = fields.CharField(
        max_length=24,
        null=True,
        description="Human-readable session_id associated with this async task.",
    )
    project_name = fields.CharField(
        max_length=128,
        null=True,
        description="Project associated with this async task, when project-scoped.",
    )
    arguments: fields.Field[dict[str, Any]] = fields.JSONField(
        default=dict,
        description="Sanitized task input arguments.",
    )
    result: fields.Field[dict[str, Any] | None] = fields.JSONField(
        null=True,
        description="Structured task result payload after successful completion.",
    )
    error_code = fields.CharField(
        max_length=128,
        null=True,
        description="Stable error code for failed tasks, when available.",
    )
    error_message = fields.TextField(
        null=True,
        description="Human-readable failure detail for failed tasks.",
    )
    started_at = fields.DatetimeField(
        null=True,
        description="UTC timestamp when task execution started.",
    )
    completed_at = fields.DatetimeField(
        null=True,
        description="UTC timestamp when task execution reached a terminal state.",
    )
    expires_at = fields.DatetimeField(
        null=True,
        description="UTC timestamp after which this task may be cleaned up.",
    )

    class Meta:
        table = "tasks"
        ordering = ["-created_at"]


class CollectLogs(DatabaseModel):
    """Persist metadata for one collect_logs artifact."""

    objects: ClassVar[CollectLogsManager[CollectLogs]] = CollectLogsManager()
    sources: fields.ReverseRelation[CollectLogsSource]

    id = fields.BigIntField(
        primary_key=True,
        description="Database-generated integer id for this collected log artifact.",
    )
    created_at = fields.DatetimeField(
        auto_now_add=True,
        description="UTC timestamp when this collected log metadata row was created.",
    )
    workspace = fields.CharEnumField(
        LogWorkspace,
        description="Collection workspace, currently 'workflow' or 'session'.",
    )
    session: fields.ForeignKeyRelation[AgentSession] = fields.ForeignKeyField(
        "models.AgentSession",
        related_name="collect_logs",
        on_delete=fields.CASCADE,
        description="Session that owns this collected log artifact.",
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
        on_delete=fields.CASCADE,
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
        storage=FileStorage(location=log_storage.location),
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
        ordering = ["id"]


class ProjectManifest(DatabaseModel):
    """Persist one project manifest with the same shape as manifest JSON."""

    objects: ClassVar[ObjectsManager[ProjectManifest]]
    cache_namespace: ClassVar[str] = "project_manifest"

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
    deployment: fields.Field[dict[str, Any] | None] = fields.JSONField(
        null=True,
        description="Optional deployment provenance metadata from the manifest.",
    )
    sources: fields.Field[list[dict[str, Any]]] = fields.JSONField(
        description="List of source definitions with the same shape as Manifest.sources.",
    )

    @classmethod
    async def clear_cache(cls) -> None:
        """Clear cached ProjectManifest query results."""

        await clear_cache_namespace(cls.cache_namespace)

    class Meta:
        table = "project_manifests"
