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

from pydantic import BaseModel, ConfigDict, RootModel

from core.types import LogWorkspace

SnapshotWorkspace = LogWorkspace


class CollectedSourcePayload(BaseModel):
    """Describe one collected manifest source in the `collect_logs` response.

    This is the per-source building block used inside `CollectLogsPayload`.
    It captures both the successful collection case and the deterministic
    degraded case where one source was unavailable.

    Important fields:

    - `status`: whether the source was actually collected
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
    - where the persisted file lives under the configured logs root
    - how large it is
    - whether it came from a docker or file-backed source
    """

    model_config = ConfigDict(extra="forbid")

    source_key: str
    source_type: Literal["docker", "file"]
    description: str
    target: str
    stream: Literal["stdout", "stderr"] | None
    parser_type: str | None = None
    normalization_profile: str | None = None
    default_noise_profile: str | None = None
    file_name: str
    output_file: str
    line_count: int
    byte_count: int

    def __getitem__(self, key: str) -> object:
        """Allow concise dict-style assertions while keeping a typed model contract."""

        return getattr(self, key)


class LogSnapshotMetadata(BaseModel):
    """Represent resolved metadata for one persisted log snapshot.

    Runtime snapshot lookup is DB-backed. This model is the in-memory contract
    passed from snapshot lookup to follow-up tools such as listing, reading,
    grepping, filtering, and grouped analysis.
    """

    model_config = ConfigDict(extra="forbid")

    project_name: str
    workspace: SnapshotWorkspace
    session_id: str | None = None
    collected_at: str
    files: list[LogSnapshotFilePayload]


class ProjectCollectLogsPayload(BaseModel):
    """Describe one per-project collection result inside `collect_logs`.

    `collect_logs` now supports multi-project collection, so each project gets
    its own persisted artifact summary inside the top-level response.
    """

    model_config = ConfigDict(extra="forbid")

    requested_project_name: str
    project_name: str
    workspace: SnapshotWorkspace
    snapshot_dir: str
    requested_source_keys: list[str]
    requested_since: str | None
    requested_until: str | None
    warnings: list[str]
    retry_tips: list[str]
    unknown_requested_source_keys: list[str]
    resolved_source_keys: list[str]
    collected_at: str
    sources: list[CollectedSourcePayload]

    def __getitem__(self, key: str) -> object:
        """Allow concise dict-style assertions while keeping a typed model contract."""

        return getattr(self, key)


class CollectLogsPayload(BaseModel):
    """Structured response returned by `collect_logs`.

    This is the main agent-facing payload for log collection. It combines:

    - request echo fields, so the caller can confirm what was asked for
    - one workspace/session context for the investigation
    - one or more per-project persisted collection artifacts
    - persisted source metadata for follow-up snapshot tools
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["collect_logs"]
    workspace: SnapshotWorkspace
    session_id: str | None
    requested_project_names: list[str]
    next_step_tips: list[str]
    projects: list[ProjectCollectLogsPayload]

    def __getitem__(self, key: str) -> object:
        """Allow concise dict-style assertions while keeping a typed model contract."""

        return getattr(self, key)


class ProjectManifestSummary(BaseModel):
    """Describe one manifest-backed project summary returned by `list_projects`.

    This is the lightweight discovery shape returned by `list_projects`. It is
    intentionally summary-oriented rather than a full manifest dump.
    """

    model_config = ConfigDict(extra="forbid")

    project_name: str
    project_summary: str
    source_keys: list[str]

    def __getitem__(self, key: str) -> object:
        """Allow concise dict-style assertions while keeping a typed model contract."""

        return getattr(self, key)


class ProjectManifestList(RootModel[list[ProjectManifestSummary]]):
    """Collection wrapper for manifest-backed project summaries."""


class ListLogSnapshotFilesPayload(BaseModel):
    """Structured response returned by `list_log_snapshot_files`.

    This payload is meant for the second step after `collect_logs` when an
    agent wants to inspect what files exist in one persisted snapshot before
    choosing a read or grep action.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["list_log_snapshot_files"]
    requested_project_name: str | None
    project_name: str
    workspace: SnapshotWorkspace
    session_id: str | None
    snapshot_dir: str
    collected_at: str
    next_step_tips: list[str]
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
    project_name: str
    workspace: SnapshotWorkspace
    session_id: str | None
    snapshot_dir: str
    source_key: str
    start_line: int | None
    line_count: int | None
    max_bytes: int
    next_step_tips: list[str]
    truncated: bool
    content: str
    output_file: str
    returned_bytes: int
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
    project_name: str
    workspace: SnapshotWorkspace
    session_id: str | None
    snapshot_dir: str
    grep: str
    searched_source_keys: list[str]
    matched_source_keys: list[str]
    match_offset: int
    max_matches: int
    match_count: int
    returned_match_count: int
    next_step_tips: list[str]
    truncated: bool
    matches: list[GrepLogSnapshotMatchPayload]


class SnapshotLineReferencePayload(BaseModel):
    """Point to one concrete line inside a persisted snapshot file.

    Analysis tools use this shape when they want to summarize or group
    findings while still giving the caller a direct pointer back to the raw
    saved file and line number.
    """

    model_config = ConfigDict(extra="forbid")

    source_key: str
    output_file: str
    line_number: int
    line: str
    line_truncated: bool


class GroupedErrorPayload(BaseModel):
    """Describe one deterministic grouped-error finding from a saved snapshot."""

    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    category: str
    severity: Literal["high", "medium", "low"]
    count: int
    source_keys: list[str]
    request_paths: list[str]
    status_codes: list[int]
    levels: list[str]
    message_summary: str
    first_timestamp: str | None
    last_timestamp: str | None
    first_seen: SnapshotLineReferencePayload
    last_seen: SnapshotLineReferencePayload


class ProxyStatusClassCountPayload(BaseModel):
    """Count HTTP proxy lines by status class."""

    model_config = ConfigDict(extra="forbid")

    status_class: Literal["1xx", "2xx", "3xx", "4xx", "5xx"]
    count: int


class ProxyRouteSignalPayload(BaseModel):
    """Describe one grouped proxy route/status signal."""

    model_config = ConfigDict(extra="forbid")

    path: str | None
    host: str | None
    method: str | None
    status_code: int
    status_class: Literal["1xx", "2xx", "3xx", "4xx", "5xx"]
    count: int
    source_keys: list[str]
    is_upstream_error: bool
    first_seen: SnapshotLineReferencePayload
    last_seen: SnapshotLineReferencePayload


class ProbeBlockingPolicyPayload(BaseModel):
    """Fail2ban policy used to decide whether probe traffic should trigger a ban."""

    model_config = ConfigDict(extra="forbid")

    findtime: str
    maxretry: int
    bantime: str


class ProbeBlockingIpPayload(BaseModel):
    """One suspicious IP correlated with fail2ban activity for one jail."""

    model_config = ConfigDict(extra="forbid")

    ip: str
    jail: str
    sources: list[str]
    request_count: int
    paths: list[str]
    last_seen: str
    maxretry: int
    expected_ban: bool
    observed_ban: bool
    ban_count: int
    unban_count: int
    already_banned_count: int
    last_ban_at: str
    last_unban_at: str


class InspectProbeBlockingActivityPayload(BaseModel):
    """Structured response returned by `inspect_probe_blocking_activity`."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["inspect_probe_blocking_activity"]
    requested_project_name: str | None
    project_name: str
    workspace: SnapshotWorkspace
    session_id: str | None
    snapshot_dir: str
    searched_source_keys: list[str]
    policy: dict[str, ProbeBlockingPolicyPayload]
    suspicious_ip_count: int
    suspicious_request_count: int
    expected_ban_ip_count: int
    observed_ban_ip_count: int
    expected_but_not_observed: list[str]
    suspicious_ips: list[ProbeBlockingIpPayload]


class Fail2banServiceStatusPayload(BaseModel):
    """Structured output from `fail2ban-client status`."""

    model_config = ConfigDict(extra="forbid")

    inspection_status: Literal["ok", "error", "unavailable"]
    jail_count: int | None
    jails: list[str]
    error: str | None


class Fail2banJailStatusPayload(BaseModel):
    """Structured output from one allowlisted fail2ban jail status command."""

    model_config = ConfigDict(extra="forbid")

    jail: str
    inspection_status: Literal["ok", "error", "unavailable"]
    currently_failed: int | None
    total_failed: int | None
    currently_banned: int | None
    total_banned: int | None
    banned_ips: list[str]
    error: str | None


class InspectLiveFail2banActivityPayload(BaseModel):
    """TODO(post-MVP): response for live fail2ban runtime diagnostics."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["inspect_live_fail2ban_activity"]
    project_name: str
    inspection_status: Literal["ok", "error", "unavailable"]
    error_code: str | None
    message: str | None
    retry_tips: list[str]
    service: Fail2banServiceStatusPayload
    jails: list[Fail2banJailStatusPayload]


class InspectProxyActivityPayload(BaseModel):
    """Structured response returned by `inspect_proxy_activity`."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["inspect_proxy_activity"]
    requested_project_name: str | None
    project_name: str
    workspace: SnapshotWorkspace
    session_id: str | None
    snapshot_dir: str
    searched_source_keys: list[str]
    total_line_count: int
    parsed_proxy_line_count: int
    http_status_line_count: int
    upstream_error_count: int
    max_groups: int
    truncated: bool
    returned_route_group_count: int
    distinct_route_group_count: int
    distinct_route_group_count_is_exact: bool
    omitted_route_group_count: int
    route_groups_omitted: bool
    status_class_counts: list[ProxyStatusClassCountPayload]
    top_routes: list[ProxyRouteSignalPayload]


class GroupErrorsPayload(BaseModel):
    """Structured response returned by `group_errors`.

    This tool condenses repeated error-like log lines into stable grouped
    findings so an agent can reason about recurring failures without reading
    every raw line individually.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["group_errors"]
    requested_project_name: str | None
    project_name: str
    workspace: SnapshotWorkspace
    session_id: str | None
    snapshot_dir: str
    searched_source_keys: list[str]
    analysis_cautions: list[str]
    next_step_tips: list[str]
    grouped_error_count: int
    matching_line_count: int
    max_groups: int
    truncated: bool
    summary: str
    groups: list[GroupedErrorPayload]


class IncidentSourceSummaryPayload(BaseModel):
    """Summarize one source's contribution to a deterministic incident bundle."""

    model_config = ConfigDict(extra="forbid")

    source_key: str
    grouped_error_count: int
    matching_line_count: int
    first_timestamp: str | None
    last_timestamp: str | None


class IncidentBundlePayload(BaseModel):
    """Structured response returned by `build_incident_bundle`.

    This is a compact deterministic bundle for LLM workflows: grouped error
    signals, source summaries, and concrete line references that point back to
    the raw persisted snapshot files.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["build_incident_bundle"]
    requested_project_name: str | None
    project_name: str
    workspace: SnapshotWorkspace
    session_id: str | None
    snapshot_dir: str
    searched_source_keys: list[str]
    analysis_cautions: list[str]
    next_step_tips: list[str]
    grouped_error_count: int
    matching_line_count: int
    high_severity_group_count: int
    medium_severity_group_count: int
    low_severity_group_count: int
    top_groups: list[GroupedErrorPayload]
    source_summaries: list[IncidentSourceSummaryPayload]
    suggested_next_steps: list[str]


class SuggestFollowupWindowPayload(BaseModel):
    """Structured response returned by `suggest_followup_window`.

    This helper converts a suspicious timestamp span from grouped analysis into
    a tighter `collect_logs` window so the caller can recollect a narrower
    snapshot around one incident period.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["suggest_followup_window"]
    first_timestamp: str
    last_timestamp: str
    padding_minutes: int
    suggested_since: str
    suggested_until: str
    suggested_duration_minutes: int
    ready_for_collect_logs: bool
    next_step_tips: list[str]
    explanation: str
    example_collect_logs_arguments: dict[str, str]


class FilteredViewSourceSummaryPayload(BaseModel):
    """Summarize one source's contribution to a deterministic cleaned view."""

    model_config = ConfigDict(extra="forbid")

    source_key: str
    total_line_count: int
    kept_line_count: int
    excluded_line_count: int
    top_exclusion_reasons: list[str]


FilteredViewMode = Literal["head", "errors", "sample"]


class CreateFilteredViewPayload(BaseModel):
    """Structured response returned by `create_filtered_view`."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["create_filtered_view"]
    requested_project_name: str | None
    project_name: str
    workspace: SnapshotWorkspace
    session_id: str | None
    snapshot_dir: str
    searched_source_keys: list[str]
    view_mode: FilteredViewMode
    max_lines: int
    total_line_count: int
    kept_line_count: int
    excluded_line_count: int
    returned_line_count: int
    next_step_tips: list[str]
    truncated: bool
    cleaned_lines: list[SnapshotLineReferencePayload]
    source_summaries: list[FilteredViewSourceSummaryPayload]


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


class ContainerHealthPayload(BaseModel):
    """One container health item returned by container diagnostics."""

    model_config = ConfigDict(extra="forbid")

    source_key: str
    inspection_status: Literal["ok", "error"]
    inspection_error: str | None
    container_name: str
    container_id: str
    image: str | None
    docker_status: str | None
    health_status: str | None
    running: bool
    restarting: bool
    paused: bool
    dead: bool
    exit_code: int | None
    error: str | None
    restart_count: int | None
    started_at: str | None
    finished_at: str | None


class InspectContainersHealthPayload(BaseModel):
    """Structured project-level success payload returned by `inspect_containers_health`."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["inspect_containers_health"]
    project_name: str
    resolved_source_keys: list[str]
    containers: list[ContainerHealthPayload]


class VpsContainerInventoryPayload(BaseModel):
    """One bounded Docker ps-style inventory item returned by VPS diagnostics."""

    model_config = ConfigDict(extra="forbid")

    container_id: str
    short_container_id: str
    container_name: str
    image: str | None
    command: list[str]
    command_preview: str
    created_at: str | None
    docker_status: str | None
    state: str | None
    health_status: str | None
    running: bool
    restarting: bool
    paused: bool
    dead: bool
    exit_code: int | None
    error: str | None
    restart_count: int | None
    started_at: str | None
    finished_at: str | None
    compose_labels: dict[str, str]
    restart_policy: ContainerRestartPolicyPayload
    ports: list[ContainerDetailPortPayload]
    network_names: list[str]
    triage_notes: list[str]


class InspectVpsContainersPayload(BaseModel):
    """Structured success payload returned by `inspect_vps_containers`."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["inspect_vps_containers"]
    container_count: int
    truncated: bool
    containers: list[VpsContainerInventoryPayload]


class VpsVolumeInventoryPayload(BaseModel):
    """One bounded Docker volume ls-style inventory item for VPS diagnostics."""

    model_config = ConfigDict(extra="forbid")

    volume_name: str
    driver: str | None
    scope: str | None
    created_at: str | None
    compose_labels: dict[str, str]
    option_keys: list[str]
    mountpoint_available: bool
    mountpoint_redacted: bool
    usage_ref_count: int | None
    usage_size_bytes: int | None


class InspectVpsVolumesPayload(BaseModel):
    """Structured success payload returned by `inspect_vps_volumes`."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["inspect_vps_volumes"]
    filters: dict[str, bool | str | None]
    volume_count: int
    truncated: bool
    volumes: list[VpsVolumeInventoryPayload]


class ContainerDetailMountPayload(BaseModel):
    """Curated mount metadata returned by `inspect_container_detail`."""

    model_config = ConfigDict(extra="forbid")

    type: str | None
    destination: str | None
    mode: str | None
    rw: bool | None


class ContainerDetailNetworkPayload(BaseModel):
    """Curated network metadata returned by `inspect_container_detail`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    ip_address: str | None
    aliases: list[str]


class ContainerDetailPortPayload(BaseModel):
    """Curated port metadata returned by `inspect_container_detail`."""

    model_config = ConfigDict(extra="forbid")

    private_port: str
    host_ip: str | None
    host_port: str | None


class ContainerDetailEnvVarPayload(BaseModel):
    """Curated environment variable metadata returned by `inspect_container_detail`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: str | None
    value_redacted: bool
    secret: bool


class ContainerRestartPolicyPayload(BaseModel):
    """Curated restart-policy metadata returned by `inspect_container_detail`."""

    model_config = ConfigDict(extra="forbid")

    name: str | None
    maximum_retry_count: int | None


class InspectContainerDetailPayload(BaseModel):
    """Structured success payload returned by `inspect_container_detail`."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["inspect_container_detail"]
    project_name: str
    source_key: str
    container: ContainerHealthPayload
    created_at: str | None
    env_var_names: list[str]
    env_vars: list[ContainerDetailEnvVarPayload]
    label_keys: list[str]
    compose_labels: dict[str, str]
    restart_policy: ContainerRestartPolicyPayload
    command: list[str]
    entrypoint: list[str]
    working_dir: str | None
    user: str | None
    ports: list[ContainerDetailPortPayload]
    mounts: list[ContainerDetailMountPayload]
    networks: list[ContainerDetailNetworkPayload]
    health_log: list[dict[str, object]]


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
    project_name: str
    source_key: str
    container_name: str
    path: str
    max_bytes: int
    truncated: bool
    content: str
    file: ContainerPathMetadataPayload


class StatContainerPathPayload(BaseModel):
    """Structured success payload returned by `stat_container_path`.

    It returns metadata for an approved file or directory without reading file
    contents or recursively listing children.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["stat_container_path"]
    requested_project_name: str | None
    project_name: str
    source_key: str
    container_name: str
    path: str
    file: ContainerPathMetadataPayload


class ListContainerDirectoryPayload(BaseModel):
    """Structured success payload returned by `list_container_directory`.

    This is the bounded path-browsing response for specialist agents. Directory
    paths return immediate children, file paths return one metadata entry, and
    neither mode acts as a recursive filesystem browser.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["list_container_directory"]
    requested_project_name: str | None
    project_name: str
    source_key: str
    container_name: str
    path: str
    truncated: bool
    entries: list[ContainerPathMetadataPayload]
