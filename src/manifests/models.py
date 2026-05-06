"""Source manifest models for project-specific log inventory."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SourceDefinition(BaseModel):
    """Describe a single log source that MCP can collect for a project."""

    source_key: str
    source_type: Literal["docker", "file"]
    target: str
    description: str
    required: bool = True
    parser_type: str
    normalization_profile: str
    retention_class: str
    default_noise_profile: str | None = None
    stream: Literal["stdout", "stderr"] | None = None
    inspect_path_prefixes: list[str] = Field(default_factory=list)


class SourceManifest(BaseModel):
    """Describe the available log sources for a single project."""

    project_key: str
    project_summary: str
    static_asset_paths: list[str] = Field(default_factory=list)
    static_asset_extensions: list[str] = Field(default_factory=list)
    sources: list[SourceDefinition] = Field(min_length=1)
