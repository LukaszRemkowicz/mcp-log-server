"""Pydantic models used by database service methods."""

from __future__ import annotations

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
    subject: str | None = None
    client_id: str | None = None
    client_type: str | None = None
    tool_name: str | None = None
    uri: str | None = None
    duration_ms: float | None = None
    success: bool = True
    error_code: str | None = None
    project_name: str | None = None
    source_keys: list[str] | None = None
    arguments: dict[str, Any] | None = None
    result_summary: dict[str, Any] | None = None


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
    duration_ms: float | None = None
    success: bool | None = None
    error_code: str | None = None
    result_summary: dict[str, Any] | None = None


class ProjectManifestUpdate(BaseModel):
    """Validated payload for updating one project manifest metadata row."""

    pk: UUID
    project_summary: str | None = None
    static_asset_paths: list[str] | None = None
    static_asset_extensions: list[str] | None = None
    sources: list[dict[str, Any]] | None = None
