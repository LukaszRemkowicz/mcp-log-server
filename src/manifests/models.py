"""Source manifest models for project-specific log inventory."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

_PATH_SCHEME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
MAX_BACKUP_LOCATIONS = 8
MAX_BACKUP_FILENAME_PATTERNS = 20


class SourceCommandRun(BaseModel):
    """Describe how MCP may run fixed project commands in one source container."""

    enabled: bool = True
    base_command: list[str] = Field(min_length=1)
    cwd: str

    @model_validator(mode="after")
    def validate_command_run_shape(self) -> SourceCommandRun:
        """Require explicit command tokens and a clean absolute workdir."""

        command = [part.strip() for part in self.base_command]
        if any(not part or any(ord(character) < 32 for character in part) for part in command):
            raise ValueError("command_run base_command must contain clean command tokens.")
        self.base_command = command

        normalized_cwd = self.cwd.replace("\\", "/")
        path_parts = normalized_cwd.split("/")[1:]
        invalid_cwd = (
            not normalized_cwd
            or not normalized_cwd.startswith("/")
            or normalized_cwd.startswith("//")
            or normalized_cwd.startswith("~")
            or _PATH_SCHEME_PATTERN.match(normalized_cwd) is not None
            or any(ord(character) < 32 for character in normalized_cwd)
            or any(part in {"", ".", ".."} for part in path_parts)
        )
        if invalid_cwd:
            raise ValueError("command_run cwd must be a clean absolute path.")
        self.cwd = normalized_cwd
        return self


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
    compose_project: str | None = None
    compose_service: str | None = None
    inspect_path_prefixes: list[str] = Field(default_factory=list)
    expected_producer_type: Literal["cron", "systemd", "docker", "app"] | None = None
    scheduler_patterns: list[str] = Field(default_factory=list)
    command_run: SourceCommandRun | None = None

    @model_validator(mode="after")
    def validate_source_shape(self) -> SourceDefinition:
        """Require clean source paths and explicit Docker Compose selectors."""

        compose_project = self.compose_project.strip() if self.compose_project else None
        compose_service = self.compose_service.strip() if self.compose_service else None
        if self.source_type == "docker":
            if compose_project is None or compose_service is None:
                raise ValueError(
                    "docker sources require compose_project and compose_service selectors."
                )
            self.compose_project = compose_project
            self.compose_service = compose_service
        elif self.compose_project is not None or self.compose_service is not None:
            raise ValueError("compose selectors are only valid for docker sources.")

        if self.command_run is not None and self.source_type != "docker":
            raise ValueError("command_run is only valid for docker sources.")

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


class ProjectBackupInspectionMetadata(BaseModel):
    """Manifest-bounded project backup metadata inspection inputs."""

    locations: list[str] = Field(min_length=1, max_length=MAX_BACKUP_LOCATIONS)
    filename_patterns: list[str] = Field(
        min_length=1,
        max_length=MAX_BACKUP_FILENAME_PATTERNS,
    )
    max_age_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_backup_inspection_shape(self) -> ProjectBackupInspectionMetadata:
        """Require absolute directories and basename-only filename patterns."""

        for location in self.locations:
            normalized_location = location.replace("\\", "/")
            path_parts = normalized_location.split("/")[1:]
            invalid_location = (
                not normalized_location
                or not normalized_location.startswith("/")
                or normalized_location.startswith("//")
                or normalized_location.startswith("~")
                or _PATH_SCHEME_PATTERN.match(normalized_location) is not None
                or any(ord(character) < 32 for character in normalized_location)
                or any(part in {"", ".", ".."} for part in path_parts)
            )
            if invalid_location:
                raise ValueError("backup inspection locations must be clean absolute paths.")

        for pattern in self.filename_patterns:
            invalid_pattern = (
                not pattern
                or pattern in {".", ".."}
                or "/" in pattern
                or "\\" in pattern
                or any(ord(character) < 32 for character in pattern)
            )
            if invalid_pattern:
                raise ValueError("backup inspection patterns must match basenames only.")
        return self


class ProjectDeploymentMetadata(BaseModel):
    """Optional project-level deployment provenance inputs."""

    compose_files: list[str] = Field(default_factory=list)
    current_tag_path: str | None = None
    expected_image_repositories: dict[str, str] = Field(default_factory=dict)
    backup_inspection: ProjectBackupInspectionMetadata | None = None

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
