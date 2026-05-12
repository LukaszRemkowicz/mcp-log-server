"""Pydantic models used only by database service methods."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AgentCallCreate(BaseModel):
    """Validated payload for creating one agent call metadata row."""

    pk: UUID = Field(default_factory=uuid4)
    session_id: UUID
    workspace: str
    event: str
    session_ended: bool = False
    client_id: str | None = None
    client_type: str | None = None
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

    session_id: UUID | None = None
    workspace: str | None = None
    event: str | None = None
    project_name: str | None = None
    success: bool | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class AgentCallUpdate(BaseModel):
    """Validated payload for updating one agent call metadata row."""

    pk: UUID
    session_ended: bool | None = None
    duration_seconds: float | None = None
    success: bool | None = None
    error_code: str | None = None


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

    session_id: UUID | None = None
    workspace: str
    project_name: str
    collected_at: datetime
    snapshot_dir: str
    metadata_file: str
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
    source_type: str
    target: str
    description: str
    stream: str | None = None
    parser_type: str | None = None
    normalization_profile: str | None = None
    default_noise_profile: str | None = None
    status: str
    file: str | None = None
    line_count: int = 0
    error: str | None = None
    retry_tips: list[str]
