"""Typed payload models shared by MCP tool modules.

This module is the common contract layer for tool responses that need more
structure than a plain `dict[str, object]`. Keeping these models together makes
it easier to:

- keep response shapes consistent across tool modules
- reuse the same payload types in tests and helper functions
- move tool implementation code without dragging model definitions along

Current groups in this file:

- log collection and snapshot payloads
- container inspection payloads
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

SnapshotWorkspace = Literal["workflow", "session"]


class CollectedSourcePayload(BaseModel):
    """Describe one collected manifest source in the `collect_logs` response.

    This is the per-source building block used inside `CollectLogsPayload`.
    It captures both the successful collection case and the deterministic
    degraded case where one source was unavailable.

    Important fields:

    - `status`: whether the source was actually collected
    - `content`: inline preview content returned to the agent
    - `content_truncated`: whether the inline preview was shortened
    - `output_file`: persisted file path when a snapshot was written
    - `retry_tips`: deterministic next-step guidance for the caller
    """

    model_config = ConfigDict(extra="forbid")

    source_key: str
    source_type: Literal["docker", "file"]
    target: str
    description: str
    stream: Literal["stdout", "stderr"] | None
    status: Literal["collected", "unavailable"]
    line_count: int
    byte_count: int
    content_truncated: bool
    content: str
    output_file: str | None
    error: str | None
    retry_tips: list[str]

    def __getitem__(self, key: str) -> object:
        """Allow concise dict-style assertions while keeping a typed model contract."""

        return getattr(self, key)


class LogSnapshotFilePayload(BaseModel):
    """Describe one saved file entry inside a persisted log snapshot.

    Snapshot follow-up tools reuse this shape so agents can understand:

    - which source produced the file
    - where the persisted file lives on disk
    - how large it is
    - whether it came from a docker or file-backed source
    """

    model_config = ConfigDict(extra="forbid")

    source_key: str
    source_type: Literal["docker", "file"]
    description: str
    target: str
    stream: Literal["stdout", "stderr"] | None
    file_name: str
    output_file: str
    line_count: int
    byte_count: int

    def __getitem__(self, key: str) -> object:
        """Allow concise dict-style assertions while keeping a typed model contract."""

        return getattr(self, key)


class LogSnapshotMetadata(BaseModel):
    """Represent the metadata JSON stored beside one persisted log snapshot.

    This model is used for the on-disk `snapshot_metadata.json` file. It is the
    durable bridge between:

    - the original `collect_logs` call
    - later follow-up calls such as listing, reading, or grepping the snapshot
    """

    model_config = ConfigDict(extra="forbid")

    project_name: str
    workspace: SnapshotWorkspace
    snapshot_id: str
    collected_at: str
    files: list[LogSnapshotFilePayload]


class CollectLogsPayload(BaseModel):
    """Structured response returned by `collect_logs`.

    This is the main agent-facing payload for log collection. It combines:

    - request echo fields, so the caller can confirm what was asked for
    - authorization/effective project fields, so project scoping is explicit
    - snapshot metadata, so later tools know what to read or search
    - inline preview content, so agents can react immediately without opening
      files for every small collection
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["collect_logs"]
    requested_project_name: str | None
    authorized_project_name: str
    effective_project_name: str
    workspace: SnapshotWorkspace
    session_id: str | None
    snapshot_id: str
    snapshot_dir: str
    metadata_file: str
    persisted: bool
    requested_source_keys: list[str]
    requested_tail_lines: int | None
    effective_tail_lines: int | None
    requested_timestamps: bool
    requested_since: str | None
    requested_until: str | None
    tail_lines_limited: bool
    warnings: list[str]
    retry_tips: list[str]
    unknown_requested_source_keys: list[str]
    resolved_source_keys: list[str]
    logs_by_source: dict[str, str]
    project_output_dir: str | None
    latest_output_dir: str | None
    archive_dir: str | None
    collected_at: str
    collected_at_file: str | None
    sources: list[CollectedSourcePayload]

    def __getitem__(self, key: str) -> object:
        """Allow concise dict-style assertions while keeping a typed model contract."""

        return getattr(self, key)


class ProjectListEntry(BaseModel):
    """Describe one project currently available through bundled manifests.

    This is the lightweight discovery shape returned by `list_projects`. It is
    intentionally summary-oriented rather than a full manifest dump.
    """

    model_config = ConfigDict(extra="forbid")

    project_name: str
    project_summary: str
    manifest_file: str
    source_keys: list[str]
    source_types: list[str]
    file_sources_available: bool
    docker_sources_available: bool

    def __getitem__(self, key: str) -> object:
        """Allow concise dict-style assertions while keeping a typed model contract."""

        return getattr(self, key)


class ListLogSnapshotFilesPayload(BaseModel):
    """Structured response returned by `list_log_snapshot_files`.

    This payload is meant for the second step after `collect_logs` when an
    agent wants to inspect what files exist in one persisted snapshot before
    choosing a read or grep action.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["list_log_snapshot_files"]
    requested_project_name: str | None
    authorized_project_name: str
    effective_project_name: str
    workspace: SnapshotWorkspace
    snapshot_id: str
    snapshot_dir: str
    metadata_file: str
    collected_at: str
    files: list[LogSnapshotFilePayload]


class ReadLogSnapshotFilePayload(BaseModel):
    """Structured response returned by `read_log_snapshot_file`.

    It combines:

    - snapshot context
    - one selected source file descriptor
    - the returned file content preview
    - a truncation flag when `max_bytes` limited the returned body
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["read_log_snapshot_file"]
    requested_project_name: str | None
    authorized_project_name: str
    effective_project_name: str
    workspace: SnapshotWorkspace
    snapshot_id: str
    snapshot_dir: str
    source_key: str
    start_line: int | None
    line_count: int | None
    max_bytes: int
    truncated: bool
    content: str
    file: LogSnapshotFilePayload


class GrepLogSnapshotMatchPayload(BaseModel):
    """Describe one single match returned from snapshot grep results.

    Each match preserves enough information for an agent to:

    - identify the source that matched
    - reopen the underlying file if needed
    - reason about the matched line in context
    """

    model_config = ConfigDict(extra="forbid")

    source_key: str
    output_file: str
    line_number: int
    line: str
    line_truncated: bool


class GrepLogSnapshotPayload(BaseModel):
    """Structured response returned by `grep_log_snapshot`.

    This payload is intentionally search-oriented rather than file-oriented. It
    summarizes:

    - what pattern was searched
    - which source files were searched
    - which source files matched
    - the bounded list of returned line matches
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["grep_log_snapshot"]
    requested_project_name: str | None
    authorized_project_name: str
    effective_project_name: str
    workspace: SnapshotWorkspace
    snapshot_id: str
    snapshot_dir: str
    grep: str
    searched_source_keys: list[str]
    matched_source_keys: list[str]
    match_offset: int
    match_limit: int
    match_count: int
    returned_match_count: int
    truncated: bool
    matches: list[GrepLogSnapshotMatchPayload]


class ContainerPathMetadataPayload(BaseModel):
    """Describe one inspected file or directory inside an approved container.

    This is the shared metadata shape used by all container-inspection tools so
    agents do not need to learn a different file/directory descriptor format
    for stat, read, and directory-list responses.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    name: str
    is_dir: bool
    size: int
    mode: int
    modified_at: str | None

    def __getitem__(self, key: str) -> object:
        """Allow concise dict-style assertions while keeping a typed model contract."""

        return getattr(self, key)


class ReadContainerFilePayload(BaseModel):
    """Structured success payload returned by `read_container_file`.

    It returns both:

    - the file metadata for the approved container path
    - the text content preview that was read from the container

    The `truncated` flag tells the caller whether `max_bytes` shortened the
    returned body.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["read_container_file"]
    requested_project_name: str | None
    authorized_project_name: str
    effective_project_name: str
    source_key: str
    container_name: str
    path: str
    max_bytes: int
    truncated: bool
    content: str
    file: ContainerPathMetadataPayload


class StatContainerPathPayload(BaseModel):
    """Structured success payload returned by `stat_container_path`.

    This is the lightest inspection response. It answers:

    - does the path exist?
    - is it a file or directory?
    - what are its basic metadata fields?

    without reading file contents or listing directory children.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["stat_container_path"]
    requested_project_name: str | None
    authorized_project_name: str
    effective_project_name: str
    source_key: str
    container_name: str
    path: str
    stat: ContainerPathMetadataPayload


class ListContainerDirectoryPayload(BaseModel):
    """Structured success payload returned by `list_container_directory`.

    This is the bounded directory-browsing response for specialist agents. It
    only returns immediate children for one approved directory and does not act
    as a recursive filesystem browser.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["list_container_directory"]
    requested_project_name: str | None
    authorized_project_name: str
    effective_project_name: str
    source_key: str
    container_name: str
    path: str
    truncated: bool
    entries: list[ContainerPathMetadataPayload]
