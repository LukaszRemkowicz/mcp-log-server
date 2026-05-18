"""Pydantic IN/OUT contracts for database service boundaries."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field

from core.types import LogWorkspace
from database.fields import FileReference
from database.types import AgentSessionStatus
from services.session_ids import SESSION_ID_MAX_LENGTH


class AgentCallCreate(BaseModel):
    """Validated payload for creating one agent call metadata row."""

    pk: UUID = Field(default_factory=uuid4)
    session_id: int
    caller: int
    event: str
    tool_name: str | None = None
    uri: str | None = None
    duration_seconds: float | None = None
    success: bool = True
    error_code: str | None = None
    project_name: str | None = None
    source_keys: list[str] | None = None
    arguments: dict[str, Any] | None = None


class AgentCallFilter(BaseModel):
    """Validated payload for filtering agent call metadata rows."""

    session_id: str | None = Field(default=None, max_length=SESSION_ID_MAX_LENGTH)
    workspace: str | None = None
    event: str | None = None
    project_name: str | None = None
    success: bool | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class AgentCallUpdate(BaseModel):
    """Validated payload for updating one agent call metadata row."""

    pk: UUID
    duration_seconds: float | None = None
    success: bool | None = None
    error_code: str | None = None


class AgentSessionCreate(BaseModel):
    """Validated payload for creating one agent session."""

    name: str = Field(max_length=SESSION_ID_MAX_LENGTH)
    caller_id: int
    status: AgentSessionStatus = AgentSessionStatus.ACTIVE


class AgentSessionOut(BaseModel):
    """Pydantic representation of one agent session row."""

    id: int
    name: str
    caller_id: int
    status: AgentSessionStatus
    closed_at: datetime | None = None


class ProjectManifestCreate(BaseModel):
    """Validated payload for creating one project manifest metadata row."""

    pk: UUID = Field(default_factory=uuid4)
    project_key: str
    project_summary: str
    static_asset_paths: list[str]
    static_asset_extensions: list[str]
    sources: list[dict[str, Any]]


class ProjectManifestUpdate(BaseModel):
    """Validated payload for updating one project manifest metadata row."""

    pk: UUID
    project_summary: str | None = None
    static_asset_paths: list[str] | None = None
    static_asset_extensions: list[str] | None = None
    sources: list[dict[str, Any]] | None = None


class CollectLogsCreate(BaseModel):
    """Validated payload for creating one collected log artifact row."""

    workspace: LogWorkspace
    session_id: int
    project_name: str
    collected_at: datetime
    snapshot_dir: str
    archive_name: str | None = None
    is_latest: bool = False
    requested_source_keys: list[str]
    resolved_source_keys: list[str]
    unknown_requested_source_keys: list[str]
    requested_since: str | None = None
    requested_until: str | None = None
    warnings: list[str]
    retry_tips: list[str]


class CollectLogsSourceCreate(BaseModel):
    """Validated payload for creating one collected source metadata row."""

    source_key: str
    source_type: Literal["docker", "file"]
    target: str
    description: str
    stream: Literal["stdout", "stderr"] | None = None
    parser_type: str | None = None
    normalization_profile: str | None = None
    default_noise_profile: str | None = None
    status: Literal["collected", "unavailable"]
    file: str | None = None
    line_count: int = 0
    error: str | None = None
    retry_tips: list[str]


class CollectLogsSourceOut(BaseModel):
    """Pydantic representation of one collect_logs_sources database row."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: int
    source_key: str
    source_type: Literal["docker", "file"]
    target: str
    description: str
    stream: Literal["stdout", "stderr"] | None = None
    parser_type: str | None = None
    normalization_profile: str | None = None
    default_noise_profile: str | None = None
    status: Literal["collected", "unavailable"]
    file: FileReference | None = None
    line_count: int
    error: str | None = None
    retry_tips: list[str]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def output_file(self) -> str | None:
        """Return the stored source file path for tool response contracts."""

        if self.file is None:
            return None
        return self.file.name

    @computed_field  # type: ignore[prop-decorator]
    @property
    def byte_count(self) -> int:
        """Return the collected source file size for tool response contracts."""

        if self.file is None:
            return 0
        return self.file.size


class CollectLogsOut(BaseModel):
    """Pydantic representation of one collect_logs database row."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: int
    session_id: str | None = None
    workspace: LogWorkspace
    caller_id: int
    project_name: str
    collected_at: datetime
    snapshot_dir: str
    archive_name: str | None = None
    is_latest: bool
    requested_source_keys: list[str]
    resolved_source_keys: list[str]
    unknown_requested_source_keys: list[str]
    requested_since: str | None = None
    requested_until: str | None = None
    warnings: list[str]
    retry_tips: list[str]


class CollectLogsWithSourcesOut(CollectLogsOut):
    """Pydantic representation of one collect_logs database row with source rows."""

    sources: list[CollectLogsSourceOut]
