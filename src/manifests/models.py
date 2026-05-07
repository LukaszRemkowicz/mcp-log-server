"""Source manifest models for project-specific log inventory."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

_PATH_SCHEME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


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

    @model_validator(mode="after")
    def validate_file_target_path_shape(self) -> SourceDefinition:
        """Reject ambiguous or expandable manifest-owned file paths.

        File source targets may be absolute, for production logs that live
        outside the project tree, or relative to the configured manifest
        directory. The manifest must not use path traversal, home expansion,
        URL/drive prefixes, UNC roots, control characters, or empty relative
        path segments.
        """

        if self.source_type != "file":
            return self

        normalized_target = self.target.replace("\\", "/")
        target_parts = normalized_target.split("/")
        is_absolute = normalized_target.startswith("/")
        relative_parts = target_parts[1:] if is_absolute else target_parts
        invalid_path = (
            not normalized_target
            or normalized_target.startswith("//")
            or normalized_target.startswith("~")
            or _PATH_SCHEME_PATTERN.match(normalized_target) is not None
            or any(ord(character) < 32 for character in normalized_target)
            or any(part in {"", ".", ".."} for part in relative_parts)
        )
        if invalid_path:
            raise ValueError("file source target must be a clean absolute or relative path.")
        return self


class Manifest(BaseModel):
    """Describe the core manifest for one project."""

    project_key: str
    project_summary: str
    static_asset_paths: list[str] = Field(default_factory=list)
    static_asset_extensions: list[str] = Field(default_factory=list)
    sources: list[SourceDefinition] = Field(min_length=1)
