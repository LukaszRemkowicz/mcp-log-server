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
    expected_producer_type: Literal["cron", "systemd", "docker", "app"] | None = None
    scheduler_patterns: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_file_target_path_shape(self) -> SourceDefinition:
        """Require clean absolute paths for manifest-owned file sources."""

        if self.source_type != "file":
            return self

        normalized_target = self.target.replace("\\", "/")
        target_parts = normalized_target.split("/")
        path_parts = target_parts[1:]
        invalid_path = (
            not normalized_target
            or not normalized_target.startswith("/")
            or normalized_target.startswith("//")
            or normalized_target.startswith("~")
            or _PATH_SCHEME_PATTERN.match(normalized_target) is not None
            or any(ord(character) < 32 for character in normalized_target)
            or any(part in {"", ".", ".."} for part in path_parts)
        )
        if invalid_path:
            raise ValueError("file source target must be a clean absolute path.")
        return self


class ProjectDeploymentMetadata(BaseModel):
    """Optional project-level deployment provenance inputs."""

    compose_files: list[str] = Field(default_factory=list)
    current_tag_path: str | None = None
    expected_image_repositories: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_path_shapes(self) -> ProjectDeploymentMetadata:
        """Require clean absolute paths for configured deployment files."""

        paths = [*self.compose_files]
        if self.current_tag_path is not None:
            paths.append(self.current_tag_path)
        for path in paths:
            normalized_path = path.replace("\\", "/")
            path_parts = normalized_path.split("/")[1:]
            invalid_path = (
                not normalized_path
                or not normalized_path.startswith("/")
                or normalized_path.startswith("//")
                or normalized_path.startswith("~")
                or _PATH_SCHEME_PATTERN.match(normalized_path) is not None
                or any(ord(character) < 32 for character in normalized_path)
                or any(part in {"", ".", ".."} for part in path_parts)
            )
            if invalid_path:
                raise ValueError("deployment paths must be clean absolute paths.")
        return self


class Manifest(BaseModel):
    """Describe the core manifest for one project."""

    project_key: str
    project_summary: str
    static_asset_paths: list[str] = Field(default_factory=list)
    static_asset_extensions: list[str] = Field(default_factory=list)
    deployment: ProjectDeploymentMetadata | None = None
    sources: list[SourceDefinition] = Field(min_length=1)
