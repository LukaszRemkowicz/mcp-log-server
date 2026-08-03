"""Deterministic grouped-error analysis over persisted log snapshots.

This module reads already persisted snapshot files and turns raw log lines into
compact, repeatable analysis payloads. The boundary is intentionally narrow:

- snapshot selection and authorization happen before this service is called
- raw collection and snapshot lifecycle live in other services
- this service parses, classifies, groups, and summarizes existing log files

The output is meant for MCP tools and agents as triage context, not as final
incident proof. Returned line references always point back to persisted raw
files so callers can verify conclusions with source context.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast

from database.fields import FileReference
from database.schemas import CollectLogsSourceOut
from services.log_filtering import is_successful_health_request
from services.log_snapshots import LogSnapshotService
from tools.models import (
    GroupedErrorPayload,
    IncidentBundlePayload,
    IncidentSourceSummaryPayload,
    InspectProbeBlockingActivityPayload,
    InspectProxyActivityPayload,
    LogSnapshotMetadata,
    ProbeBlockingBanPayload,
    ProbeBlockingIpPayload,
    ProbeBlockingPolicyPayload,
    ProxyRouteSignalPayload,
    ProxyStatusClassCountPayload,
    SnapshotLineReferencePayload,
)

MAX_ANALYSIS_LINE_BYTES = 2000
MIN_PROXY_ROUTE_CANDIDATES = 32
PROXY_ROUTE_CANDIDATE_MULTIPLIER = 4
LABELED_CODE_HASH_PREFIX_LENGTH = 12
GROUP_ERRORS_ANALYSIS_GROUP_LIMIT = 5_000
GROUP_ERRORS_FINGERPRINT_VERSION: Literal["group-errors-v2"] = "group-errors-v2"
StatusClass = Literal["1xx", "2xx", "3xx", "4xx", "5xx"]
STATUS_CLASSES: tuple[StatusClass, ...] = ("1xx", "2xx", "3xx", "4xx", "5xx")
ProxyRouteKey = tuple[str | None, str | None, str | None, int]
FingerprintIdentityKind = Literal[
    "explicit_message",
    "http_summary",
    "structured_semantic",
    "raw_fallback",
    "plain_text_message",
]

_UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_LONG_HEX_PATTERN = re.compile(
    r"\b(?=[0-9a-f]{12,}\b)(?=[0-9a-f]*[a-f])[0-9a-f]{12,}\b",
    re.IGNORECASE,
)
_LONG_NUMBER_PATTERN = re.compile(r"\b\d{6,}\b")
_LABELED_NUMERIC_CODE_PREFIX = re.compile(
    r"(?:error(?:[\s_-]*code)?|code|errno|sqlstate|status|exit[\s_-]*code)" r"\s*[:=#-]?\s*$",
    re.IGNORECASE,
)
_RAW_FALLBACK_NUMBER_PATTERN = re.compile(r"\b\d{2,}\b")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_HTTP_METHOD_TOKEN_PATTERN = r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+"
_REQUEST_LINE_PATTERN = re.compile(rf"^{_HTTP_METHOD_TOKEN_PATTERN}\s+(\S+)\s+HTTP/\d(?:\.\d)?$")
_REQUEST_METHOD_PATH_PATTERN = re.compile(
    rf"^(?P<method>{_HTTP_METHOD_TOKEN_PATTERN})\s+(?P<path>\S+)"
)
_DOCKER_JSON_LINE_PATTERN = re.compile(r"^\S+\s+({.*})\s*$")
_UPSTREAM_EVIDENCE_FIELDS = (
    "upstream_addr",
    "upstream",
    "ServiceURL",
    "serviceURL",
    "upstream_status",
    "OriginStatus",
    "originStatus",
)
_PLAIN_TEXT_LOG_PREFIX_PATTERN = re.compile(
    r"^(?P<timestamp>"
    r"(?:"
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    r"|\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}"
    r")"
    r")\s+"
    r"(?:(?:stdout|stderr)(?:\s+[FP])?\s+)?"
    r"(?P<message>.*)$"
)
_PROBE_BLOCKING_POLICY = ProbeBlockingPolicyPayload(
    scenario="appsec/second-probe",
    maintained_appsec_detection_threshold=2,
    detection_window="1m",
    ban_duration="876000h",
    effective_permanent_ban=True,
)
_PROBE_SUSPICIOUS_PATH_PATTERNS = (
    re.compile(r"^/(?:[^/]+/)*\.env[^ ]*$"),
    re.compile(r"^/(?:[^/]+/)*\.git/config[^ ]*$"),
    re.compile(
        r"^/(?:wp-admin|wp-login|wp-content|xmlrpc\.php|phpMyAdmin|phpmyadmin|"
        r"setup\.php|config\.php|eval-stdin\.php)(?:[/?].*)?$"
    ),
)
_CROWDSEC_APPSEC_SECOND_PROBE_BAN_PATTERN = re.compile(
    r'\btime="(?P<timestamp>[^"]+)".*\bmsg="[^"]*\bappsec/second-probe '
    r"\bby ip (?P<ip>\S+) "
    r'[^"]*:\s+[^"]*\bban on Ip (?P=ip)\b'
)


class StructuredLogField(StrEnum):
    """Whitelisted JSON field names recognized by structured log analysis.

    Structured classification intentionally supports a small field vocabulary
    instead of inspecting arbitrary JSON keys. That keeps grouping predictable
    across sources with slightly different log shapes.
    """

    LEVEL = "level"
    SEVERITY = "severity"
    MESSAGE = "message"
    ERROR = "error"
    MSG = "msg"
    STATUS_CODE = "status_code"
    STATUS = "status"
    DOWNSTREAM_STATUS = "DownstreamStatus"
    REQUEST_PATH = "request_path"
    TRAEFIK_REQUEST_PATH = "RequestPath"
    PATH = "path"
    REQUEST = "request"
    METHOD = "method"
    REQUEST_METHOD = "request_method"
    TRAEFIK_REQUEST_METHOD = "RequestMethod"
    HOST = "host"
    REQUEST_HOST = "request_host"
    TRAEFIK_REQUEST_HOST = "RequestHost"
    EVENT = "event"
    OPERATION = "operation"
    ERROR_CATEGORY = "error_category"
    ERROR_CODE = "error_code"
    TIMESTAMP = "timestamp"
    TIME = "time"
    TIME_LOCAL = "time_local"
    TS = "ts"


TIMESTAMP_FIELDS = (
    StructuredLogField.TIMESTAMP,
    StructuredLogField.TIME,
    StructuredLogField.TIME_LOCAL,
    StructuredLogField.TS,
)
STRUCTURED_SEMANTIC_FIELDS = (
    StructuredLogField.EVENT,
    StructuredLogField.OPERATION,
    StructuredLogField.ERROR_CATEGORY,
    StructuredLogField.ERROR_CODE,
    StructuredLogField.STATUS_CODE,
    StructuredLogField.STATUS,
    StructuredLogField.DOWNSTREAM_STATUS,
)


def _normalize_text(value: str) -> str:
    """Normalize noisy identifiers so similar messages group together.

    UUIDs, long hashes, long numeric ids, and repeated whitespace are replaced
    before fingerprinting. This prevents equivalent failures from becoming
    separate groups only because they contain request ids, container ids, or
    generated object ids.
    """

    value = _UUID_PATTERN.sub("<uuid>", value)
    value = _LONG_HEX_PATTERN.sub("<id>", value)
    value = _LONG_NUMBER_PATTERN.sub(_normalize_long_number, value)
    value = _WHITESPACE_PATTERN.sub(" ", value.strip())
    return value


def _normalize_long_number(match: re.Match[str]) -> str:
    """Keep bounded labeled codes while removing volatile numeric identifiers."""

    value = match.group(0)
    prefix = match.string[max(0, match.start() - 48) : match.start()]
    if _LABELED_NUMERIC_CODE_PREFIX.search(prefix):
        if len(value) <= 12:
            return value
        code_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return f"<code:{code_hash[:LABELED_CODE_HASH_PREFIX_LENGTH]}>"
    return "<n>"


def _normalize_raw_fallback(value: str) -> str:
    """Normalize volatile metadata only when raw structured text is the identity."""

    value = _UUID_PATTERN.sub("<uuid>", value)
    value = _LONG_HEX_PATTERN.sub("<id>", value)
    value = _RAW_FALLBACK_NUMBER_PATTERN.sub("<n>", value)
    return _WHITESPACE_PATTERN.sub(" ", value.strip())


def _truncate_line(value: str) -> tuple[str, bool]:
    """Bound one returned analysis line for agent-facing payloads."""

    encoded = value.encode("utf-8")
    if len(encoded) <= MAX_ANALYSIS_LINE_BYTES:
        return value, False
    return encoded[:MAX_ANALYSIS_LINE_BYTES].decode("utf-8", errors="ignore"), True


def _extract_request_path(value: str | None) -> str | None:
    """Extract a request path from a direct path or one HTTP request line."""

    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if value.startswith("/"):
        return value
    match = _REQUEST_LINE_PATTERN.match(value)
    if match:
        return match.group(1)
    return None


def _extract_plain_text_message(value: str) -> tuple[str, str | None]:
    """Remove only a recognized leading runtime timestamp from text identity."""

    match = _PLAIN_TEXT_LOG_PREFIX_PATTERN.match(value)
    if match is None:
        return value, None
    return match.group("message"), match.group("timestamp")


def _build_http_message_summary(
    *,
    status_code: int | None,
    request_method: str | None,
    request_host: str | None,
    request_path: str | None,
) -> str | None:
    """Build a compact stable summary for message-less HTTP access events."""

    if status_code is None or request_path is None:
        return None
    parts = ["HTTP", str(status_code)]
    if request_method:
        parts.append(request_method)
    if request_host:
        parts.append(request_host)
    parts.append(request_path)
    return " ".join(parts)


def _build_structured_semantic_metadata(
    payload: dict[str, Any],
    *,
    status_code: int | None,
) -> tuple[str, str]:
    """Build bounded display metadata and exact canonical semantic identity."""

    parts: list[str] = []
    canonical_fields: list[tuple[str, str | int | float | bool]] = []
    for field_name in STRUCTURED_SEMANTIC_FIELDS:
        raw_value = payload.get(field_name)
        if not isinstance(raw_value, str | int | float | bool):
            continue
        if field_name in {
            StructuredLogField.STATUS_CODE,
            StructuredLogField.STATUS,
            StructuredLogField.DOWNSTREAM_STATUS,
        }:
            try:
                if int(raw_value) == status_code:
                    continue
            except (TypeError, ValueError):
                pass
        canonical_value: str | int | float | bool = raw_value
        if isinstance(canonical_value, str):
            canonical_value = canonical_value.strip()
            if not canonical_value:
                continue
        canonical_fields.append((field_name.value, canonical_value))
        semantic_value = _WHITESPACE_PATTERN.sub(" ", str(canonical_value))
        if semantic_value:
            parts.append(_normalize_text(f"{field_name.value}={semantic_value}"))
    semantic_summary, _ = _truncate_line(" ".join(parts))
    if not canonical_fields:
        return semantic_summary, ""
    canonical_identity = json.dumps(
        canonical_fields,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    semantic_identity_hash = hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()
    return semantic_summary, semantic_identity_hash


def _snapshot_dir_from_metadata(metadata: LogSnapshotMetadata) -> str:
    """Return the relative snapshot directory represented by snapshot metadata."""

    if not metadata.files:
        return ""
    return Path(metadata.files[0].output_file).parent.as_posix()


@dataclass(slots=True)
class ErrorEvent:
    """One classified error-like event extracted from a raw snapshot line.

    This is the internal shape between classification and grouping. It keeps
    the raw line reference, normalized grouping data, optional HTTP fields, and
    severity/category decisions needed to build public grouped payloads.
    """

    source_key: str
    output_file: str
    line_number: int
    line: str
    line_truncated: bool
    category: str
    severity: Literal["high", "medium", "low"]
    fingerprint: str
    message_summary: str
    timestamp: str | None
    request_path: str | None
    request_method: str | None
    request_host: str | None
    status_code: int | None
    level: str | None
    has_explicit_message: bool
    identity_kind: FingerprintIdentityKind
    semantic_summary: str
    semantic_identity_hash: str
    upstream_attempted: bool | None


@dataclass(slots=True)
class ErrorGroupAccumulator:
    """Mutable grouping state before conversion into a public payload model.

    Events sharing the same fingerprint are merged here. The accumulator keeps
    aggregate counts plus first/last raw line references so the final response
    remains compact while still pointing back to verifiable source lines.
    """

    fingerprint: str
    category: str
    severity: Literal["high", "medium", "low"]
    message_summary: str
    identity_kind: FingerprintIdentityKind
    semantic_summary: str
    semantic_identity_hash: str
    upstream_attempted: bool | None
    count: int = 0
    source_keys: set[str] = field(default_factory=set)
    request_paths: set[str] = field(default_factory=set)
    request_methods: set[str] = field(default_factory=set)
    request_hosts: set[str] = field(default_factory=set)
    status_codes: set[int] = field(default_factory=set)
    levels: set[str] = field(default_factory=set)
    has_explicit_message: bool = False
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    first_seen: SnapshotLineReferencePayload | None = None
    last_seen: SnapshotLineReferencePayload | None = None

    def add(self, event: ErrorEvent) -> None:
        """Merge one classified event into this fingerprint group."""

        self.count += 1
        self.source_keys.add(event.source_key)
        if event.request_path:
            self.request_paths.add(event.request_path)
        if event.request_method:
            self.request_methods.add(event.request_method)
        if event.request_host:
            self.request_hosts.add(event.request_host)
        if event.status_code is not None:
            self.status_codes.add(event.status_code)
        if event.level:
            self.levels.add(event.level)
        self.has_explicit_message = self.has_explicit_message or event.has_explicit_message
        if event.timestamp is not None and self.first_timestamp is None:
            self.first_timestamp = event.timestamp
        if event.timestamp is not None:
            self.last_timestamp = event.timestamp

        reference = SnapshotLineReferencePayload(
            source_key=event.source_key,
            output_file=event.output_file,
            line_number=event.line_number,
            line=event.line,
            line_truncated=event.line_truncated,
        )
        if self.first_seen is None:
            self.first_seen = reference
        self.last_seen = reference

    def to_payload(self) -> GroupedErrorPayload:
        """Convert accumulated state into the public grouped-error payload."""

        assert self.first_seen is not None
        assert self.last_seen is not None
        return GroupedErrorPayload(
            fingerprint=self.fingerprint,
            category=self.category,
            severity=self.severity,
            count=self.count,
            source_keys=sorted(self.source_keys),
            request_paths=sorted(self.request_paths),
            request_methods=sorted(self.request_methods),
            request_hosts=sorted(self.request_hosts),
            status_codes=sorted(self.status_codes),
            levels=sorted(self.levels),
            message_summary=self.message_summary,
            has_explicit_message=self.has_explicit_message,
            identity_kind=self.identity_kind,
            semantic_summary=self.semantic_summary,
            semantic_identity_hash=self.semantic_identity_hash,
            upstream_attempted=self.upstream_attempted,
            first_timestamp=self.first_timestamp,
            last_timestamp=self.last_timestamp,
            first_seen=self.first_seen,
            last_seen=self.last_seen,
        )


@dataclass(slots=True)
class ProxyLineEvent:
    """One parsed proxy-like snapshot line before aggregation."""

    source_key: str
    output_file: str
    line_number: int
    line: str
    line_truncated: bool
    timestamp: str | None
    status_code: int | None
    status_class: StatusClass | None
    host: str | None
    method: str | None
    path: str | None
    client_ip: str | None
    user_agent: str | None
    upstream_attempted: bool | None


@dataclass(slots=True)
class ProxyRouteAccumulator:
    """Mutable route/status grouping state for proxy diagnostics."""

    path: str | None
    host: str | None
    method: str | None
    status_code: int
    status_class: StatusClass
    count: int = 0
    upstream_attempt_count: int = 0
    non_upstream_count: int = 0
    unknown_upstream_count: int = 0
    source_keys: set[str] = field(default_factory=set)
    first_seen: SnapshotLineReferencePayload | None = None
    last_seen: SnapshotLineReferencePayload | None = None

    def add(self, event: ProxyLineEvent) -> None:
        """Merge one proxy event into this route/status group."""

        self.count += 1
        if event.upstream_attempted is True:
            self.upstream_attempt_count += 1
        elif event.upstream_attempted is False:
            self.non_upstream_count += 1
        else:
            self.unknown_upstream_count += 1
        self.source_keys.add(event.source_key)
        reference = SnapshotLineReferencePayload(
            source_key=event.source_key,
            output_file=event.output_file,
            line_number=event.line_number,
            line=event.line,
            line_truncated=event.line_truncated,
        )
        if self.first_seen is None:
            self.first_seen = reference
        self.last_seen = reference

    def to_payload(self) -> ProxyRouteSignalPayload:
        """Convert accumulated state into the public proxy route payload."""

        assert self.first_seen is not None
        assert self.last_seen is not None
        return ProxyRouteSignalPayload(
            path=self.path,
            host=self.host,
            method=self.method,
            status_code=self.status_code,
            status_class=self.status_class,
            count=self.count,
            source_keys=sorted(self.source_keys),
            upstream_attempt_count=self.upstream_attempt_count,
            non_upstream_count=self.non_upstream_count,
            unknown_upstream_count=self.unknown_upstream_count,
            is_upstream_error=(
                self.status_code in {502, 503, 504} and self.upstream_attempt_count > 0
            ),
            first_seen=self.first_seen,
            last_seen=self.last_seen,
        )


@dataclass(slots=True)
class ProxyRouteCandidate:
    """Bounded first-pass candidate for later exact route aggregation."""

    estimated_count: int
    is_upstream_error: bool


@dataclass(frozen=True, slots=True)
class GroupedSnapshotAnalysis:
    """Grouped-error analysis result before tool-specific response shaping.

    Keeping this as a named object avoids positional tuple unpacking in callers
    and makes result completeness explicit:

    - returned groups after `max_groups` truncation
    - total matching error-like line count
    - source keys that were actually searched
    - retained group count before page truncation
    - whether the entire selected snapshot was examined
    - the distinct-group safety limit used for the scan
    """

    groups: list[GroupedErrorPayload]
    matching_line_count: int
    searched_source_keys: list[str]
    total_group_count: int
    analysis_complete: bool
    analysis_group_limit: int


@dataclass(frozen=True, slots=True)
class ProxyActivityAnalysis:
    """Aggregated proxy diagnostics before tool-specific response shaping."""

    searched_source_keys: list[str]
    total_line_count: int
    parsed_proxy_line_count: int
    excluded_health_check_count: int
    http_status_line_count: int
    upstream_error_count: int
    status_class_counts: list[ProxyStatusClassCountPayload]
    top_routes: list[ProxyRouteSignalPayload]
    total_route_group_count: int
    route_group_count_is_exact: bool


@dataclass(slots=True)
class ProbeBlockingRecord:
    """Mutable suspicious-access context and AppSec evidence for one IP."""

    ip: str
    sources: set[str] = field(default_factory=set)
    suspicious_access_count: int = 0
    paths: set[str] = field(default_factory=set)
    last_seen: str = ""
    appsec_ban_count: int = 0
    last_appsec_ban_at: str = ""

    def to_payload(self) -> ProbeBlockingIpPayload:
        """Convert accumulated context into the public CrowdSec payload."""

        return ProbeBlockingIpPayload(
            ip=self.ip,
            sources=sorted(self.sources),
            suspicious_access_count=self.suspicious_access_count,
            paths=sorted(self.paths),
            last_seen=self.last_seen,
            observed_appsec_ban=self.appsec_ban_count > 0,
            appsec_ban_count=self.appsec_ban_count,
            last_appsec_ban_at=self.last_appsec_ban_at,
        )

    def to_ban_payload(self) -> ProbeBlockingBanPayload:
        """Convert confirmed AppSec ban evidence into its public payload."""

        return ProbeBlockingBanPayload(
            ip=self.ip,
            appsec_ban_count=self.appsec_ban_count,
            last_appsec_ban_at=self.last_appsec_ban_at,
            has_suspicious_access_context=self.suspicious_access_count > 0,
        )


class LogAnalysisService:
    """Run deterministic grouped-error analysis over persisted snapshots.

    Responsibility:

    - read the already authorized persisted snapshot files
    - classify error-like lines into deterministic categories
    - group recurring failures into compact findings
    - build a structured incident bundle on top of those grouped findings

    This service does not collect logs and does not own snapshot lifecycle.
    It assumes the caller has already selected and authorized a persisted
    snapshot through `LogSnapshotService`.
    """

    def __init__(self, snapshot_service: LogSnapshotService | None = None) -> None:
        self.snapshot_service = snapshot_service or LogSnapshotService()

    def group_snapshot_errors(
        self,
        sources: list[CollectLogsSourceOut],
        requested_source_keys: list[str] | None,
        max_groups: int,
        offset: int = 0,
        analysis_group_limit: int = GROUP_ERRORS_ANALYSIS_GROUP_LIMIT,
    ) -> GroupedSnapshotAnalysis:
        """Group repeated error-like lines from selected persisted source files.

        The caller passes already-loaded snapshot metadata. This method resolves
        the selected source files from their relative metadata paths, classifies
        each line, groups matching events by deterministic fingerprint, and
        returns the requested page plus completeness metadata. When the
        distinct-group safety limit is reached, matching-line and group counts
        are lower bounds over the portion of the snapshot examined.

        Raises:
            ValueError: When `requested_source_keys` contains a key that is not
                present in the collected source objects, or when one persisted source file cannot
                be safely resolved on disk.
        """

        if analysis_group_limit < 1:
            raise ValueError("analysis_group_limit must be a positive integer.")

        selected_files = self._select_snapshot_files(
            requested_source_keys,
            sources=sources,
        )
        groups: dict[str, ErrorGroupAccumulator] = {}
        matching_line_count = 0
        searched_source_keys: list[str] = []
        analysis_complete = True

        for item in selected_files:
            file_ref = cast(FileReference, item.file)
            is_proxy_source = self._is_proxy_source(item)
            try:
                with open(file_ref.path, encoding="utf-8", errors="replace") as file:
                    searched_source_keys.append(item.source_key)
                    for line_number, raw_line in enumerate(file, start=1):
                        event = self._classify_line(
                            source_key=item.source_key,
                            output_file=file_ref.name,
                            line_number=line_number,
                            raw_line=raw_line.rstrip("\n"),
                            is_proxy_source=is_proxy_source,
                        )
                        if event is None:
                            continue
                        matching_line_count += 1
                        group = groups.get(event.fingerprint)
                        if group is None:
                            if len(groups) >= analysis_group_limit:
                                analysis_complete = False
                                break
                            group = ErrorGroupAccumulator(
                                fingerprint=event.fingerprint,
                                category=event.category,
                                severity=event.severity,
                                message_summary=event.message_summary,
                                identity_kind=event.identity_kind,
                                semantic_summary=event.semantic_summary,
                                semantic_identity_hash=event.semantic_identity_hash,
                                upstream_attempted=event.upstream_attempted,
                            )
                            groups[event.fingerprint] = group
                        group.add(event)
            except ValueError as error:
                raise ValueError("Requested persisted source file reference is invalid.") from error
            except OSError as error:
                raise ValueError("Requested log snapshot file was not found on disk.") from error
            if not analysis_complete:
                break

        sorted_groups = sorted(
            (group.to_payload() for group in groups.values()),
            key=lambda payload: (
                self._severity_rank(payload.severity),
                -payload.count,
                payload.fingerprint,
            ),
        )
        returned_groups = sorted_groups[offset : offset + max_groups]
        return GroupedSnapshotAnalysis(
            groups=returned_groups,
            matching_line_count=matching_line_count,
            searched_source_keys=searched_source_keys,
            total_group_count=len(sorted_groups),
            analysis_complete=analysis_complete,
            analysis_group_limit=analysis_group_limit,
        )

    def build_incident_bundle(
        self,
        metadata: LogSnapshotMetadata,
        *,
        sources: list[CollectLogsSourceOut],
        requested_source_keys: list[str] | None,
        max_groups: int,
        analysis_group_limit: int = GROUP_ERRORS_ANALYSIS_GROUP_LIMIT,
        requested_project_name: str | None,
        project_name: str,
        analysis_cautions: list[str],
        next_step_tips: list[str],
    ) -> IncidentBundlePayload:
        """Build one deterministic incident bundle from snapshot metadata.

        This service method does not load snapshots, authorize projects, or
        call MCP APIs. It assumes the caller has already selected the snapshot
        and passed its parsed metadata.

        The bundle is built from `group_snapshot_errors(...)` and adds a
        higher-level summary layer:

        - source-level grouped-error and matching-line counts
        - high/medium/low severity totals
        - the top grouped findings capped by `max_groups`
        - deterministic follow-up suggestions based on the strongest group

        The result is meant for incident triage. It compresses repeated signals
        into a compact payload, while preserving line references so callers can
        verify the raw log context before making conclusions.
        """

        analysis = self.group_snapshot_errors(
            sources=sources,
            requested_source_keys=requested_source_keys,
            max_groups=analysis_group_limit,
            analysis_group_limit=analysis_group_limit,
        )
        retained_groups = analysis.groups
        top_groups = retained_groups[:max_groups]
        high_count = sum(1 for group in retained_groups if group.severity == "high")
        medium_count = sum(1 for group in retained_groups if group.severity == "medium")
        low_count = sum(1 for group in retained_groups if group.severity == "low")

        effective_next_step_tips = list(next_step_tips)
        if not analysis.analysis_complete:
            effective_next_step_tips.append(
                "Grouped-analysis counts are lower bounds because the safety limit "
                f"of {analysis.analysis_group_limit} groups was reached. Recollect a "
                "narrower time window or select fewer sources before drawing complete totals."
            )
        suggested_next_steps = self._build_suggested_next_steps(top_groups)
        return IncidentBundlePayload(
            action="build_incident_bundle",
            fingerprint_version=GROUP_ERRORS_FINGERPRINT_VERSION,
            requested_project_name=requested_project_name,
            project_name=project_name,
            workspace=metadata.workspace,
            session_id=metadata.session_id,
            snapshot_dir=_snapshot_dir_from_metadata(metadata),
            searched_source_keys=analysis.searched_source_keys,
            analysis_cautions=analysis_cautions,
            next_step_tips=effective_next_step_tips,
            grouped_error_count=analysis.total_group_count,
            matching_line_count=analysis.matching_line_count,
            analysis_complete=analysis.analysis_complete,
            analysis_group_limit=analysis.analysis_group_limit,
            high_severity_group_count=high_count,
            medium_severity_group_count=medium_count,
            low_severity_group_count=low_count,
            top_groups=top_groups,
            source_summaries=self._build_source_summaries(retained_groups),
            suggested_next_steps=suggested_next_steps,
        )

    def inspect_probe_blocking_activity(
        self,
        metadata: LogSnapshotMetadata,
        *,
        sources: list[CollectLogsSourceOut],
        requested_source_keys: list[str] | None,
        requested_project_name: str | None,
        project_name: str,
    ) -> InspectProbeBlockingActivityPayload:
        """Report AppSec second-probe bans and correlated suspicious access context."""

        selected_sources = self._select_snapshot_files(
            requested_source_keys,
            sources=sources,
        )
        records: dict[str, ProbeBlockingRecord] = {}
        searched_source_keys: list[str] = []

        for source in selected_sources:
            source_key = source.source_key
            searched_source_keys.append(source_key)
            is_proxy_access = source.normalization_profile == "proxy_access"
            if source_key != "crowdsec_runtime" and not is_proxy_access:
                continue
            file_ref = cast(FileReference, source.file)
            try:
                with open(file_ref.path, encoding="utf-8", errors="replace") as file:
                    for line_number, raw_line in enumerate(file, start=1):
                        line = raw_line.rstrip("\n")
                        if source_key == "crowdsec_runtime":
                            self._add_crowdsec_appsec_ban_event(line, records)
                            continue
                        payload = self._parse_json_line(line)
                        if payload is None:
                            continue
                        event = self._parse_proxy_line(
                            source_key=source_key,
                            output_file=file_ref.name,
                            line_number=line_number,
                            raw_line=line,
                            payload=payload,
                        )
                        self._add_sensitive_probe_event(event, records)
            except ValueError as error:
                raise ValueError("Requested persisted source file reference is invalid.") from error
            except OSError as error:
                raise ValueError("Requested log snapshot file was not found on disk.") from error

        suspicious_ips = sorted(
            (
                record.to_payload()
                for record in records.values()
                if record.suspicious_access_count > 0
            ),
            key=lambda item: (-item.suspicious_access_count, item.ip),
        )
        appsec_bans = sorted(
            (record.to_ban_payload() for record in records.values() if record.appsec_ban_count > 0),
            key=lambda item: item.ip,
        )
        return InspectProbeBlockingActivityPayload(
            action="inspect_probe_blocking_activity",
            requested_project_name=requested_project_name,
            project_name=project_name,
            workspace=metadata.workspace,
            session_id=metadata.session_id,
            snapshot_dir=_snapshot_dir_from_metadata(metadata),
            searched_source_keys=searched_source_keys,
            policy=_PROBE_BLOCKING_POLICY,
            suspicious_ip_count=len(suspicious_ips),
            suspicious_access_count=sum(item.suspicious_access_count for item in suspicious_ips),
            observed_appsec_ban_ip_count=len(appsec_bans),
            appsec_bans=appsec_bans,
            suspicious_ips=suspicious_ips,
        )

    def inspect_proxy_activity(
        self,
        metadata: LogSnapshotMetadata,
        *,
        sources: list[CollectLogsSourceOut],
        requested_source_keys: list[str] | None,
        max_groups: int,
        requested_project_name: str | None,
        project_name: str,
    ) -> InspectProxyActivityPayload:
        """Build deterministic ingress/proxy diagnostics from persisted snapshots."""

        selected_sources = self._select_proxy_snapshot_files(
            requested_source_keys,
            sources=sources,
        )
        analysis = self._analyze_proxy_activity(
            sources=selected_sources,
            max_groups=max_groups,
        )
        return InspectProxyActivityPayload(
            action="inspect_proxy_activity",
            requested_project_name=requested_project_name,
            project_name=project_name,
            workspace=metadata.workspace,
            session_id=metadata.session_id,
            snapshot_dir=_snapshot_dir_from_metadata(metadata),
            searched_source_keys=analysis.searched_source_keys,
            total_line_count=analysis.total_line_count,
            parsed_proxy_line_count=analysis.parsed_proxy_line_count,
            excluded_health_check_count=analysis.excluded_health_check_count,
            http_status_line_count=analysis.http_status_line_count,
            upstream_error_count=analysis.upstream_error_count,
            max_groups=max_groups,
            truncated=analysis.total_route_group_count > max_groups,
            returned_route_group_count=len(analysis.top_routes),
            distinct_route_group_count=analysis.total_route_group_count,
            distinct_route_group_count_is_exact=analysis.route_group_count_is_exact,
            omitted_route_group_count=max(
                0,
                analysis.total_route_group_count - len(analysis.top_routes),
            ),
            route_groups_omitted=analysis.total_route_group_count > max_groups,
            status_class_counts=analysis.status_class_counts,
            top_routes=analysis.top_routes,
        )

    def _analyze_proxy_activity(
        self,
        *,
        sources: list[CollectLogsSourceOut],
        max_groups: int,
    ) -> ProxyActivityAnalysis:
        """Aggregate status classes and route/status clusters for proxy logs."""

        route_candidates: dict[ProxyRouteKey, ProxyRouteCandidate] = {}
        status_counts: dict[StatusClass, int] = defaultdict(int)
        total_line_count = 0
        parsed_proxy_line_count = 0
        excluded_health_check_count = 0
        http_status_line_count = 0
        upstream_error_count = 0

        candidate_limit = self._proxy_route_candidate_limit(max_groups)
        route_group_overflowed = False
        for event in self._iter_proxy_events(sources):
            total_line_count += 1
            if event is None:
                continue
            parsed_proxy_line_count += 1
            status_code = event.status_code
            if is_successful_health_request(event.path, status_code):
                excluded_health_check_count += 1
                continue
            status_class = event.status_class
            if status_code is None or status_class is None:
                continue
            http_status_line_count += 1
            status_counts[status_class] += 1
            is_upstream_error = status_code in {502, 503, 504} and event.upstream_attempted is True
            if is_upstream_error:
                upstream_error_count += 1
            route_group_overflowed = (
                self._record_proxy_route_candidate(
                    route_candidates,
                    route_key=self._proxy_route_key(event),
                    is_upstream_error=is_upstream_error,
                    candidate_limit=candidate_limit,
                )
                or route_group_overflowed
            )

        route_groups = self._build_proxy_route_groups_for_candidates(
            sources=sources,
            route_keys=set(route_candidates),
        )

        sorted_routes = sorted(
            (group.to_payload() for group in route_groups.values()),
            key=lambda route: (
                -route.count,
                not route.is_upstream_error,
                -route.status_code,
                route.path or "",
            ),
        )
        return ProxyActivityAnalysis(
            searched_source_keys=[item.source_key for item in sources],
            total_line_count=total_line_count,
            parsed_proxy_line_count=parsed_proxy_line_count,
            excluded_health_check_count=excluded_health_check_count,
            http_status_line_count=http_status_line_count,
            upstream_error_count=upstream_error_count,
            status_class_counts=[
                ProxyStatusClassCountPayload(
                    status_class=status_class,
                    count=status_counts[status_class],
                )
                for status_class in STATUS_CLASSES
                if status_counts[status_class] > 0
            ],
            top_routes=sorted_routes[:max_groups],
            total_route_group_count=len(route_candidates) + int(route_group_overflowed),
            route_group_count_is_exact=not route_group_overflowed,
        )

    @staticmethod
    def _proxy_route_candidate_limit(max_groups: int) -> int:
        """Return the bounded number of route candidates retained during scan."""

        return max(MIN_PROXY_ROUTE_CANDIDATES, max_groups * PROXY_ROUTE_CANDIDATE_MULTIPLIER)

    def _iter_proxy_events(
        self,
        sources: list[CollectLogsSourceOut],
    ) -> Iterator[ProxyLineEvent | None]:
        """Yield parsed proxy events, using None for non-JSON snapshot lines."""

        for item in sources:
            file_ref = cast(FileReference, item.file)
            try:
                with open(file_ref.path, encoding="utf-8", errors="replace") as file:
                    for line_number, raw_line in enumerate(file, start=1):
                        line = raw_line.rstrip("\n")
                        payload = self._parse_json_line(line)
                        if payload is None:
                            yield None
                            continue
                        yield self._parse_proxy_line(
                            source_key=item.source_key,
                            output_file=file_ref.name,
                            line_number=line_number,
                            raw_line=line,
                            payload=payload,
                        )
            except ValueError as error:
                raise ValueError("Requested persisted source file reference is invalid.") from error
            except OSError as error:
                raise ValueError("Requested log snapshot file was not found on disk.") from error

    @staticmethod
    def _proxy_route_key(event: ProxyLineEvent) -> ProxyRouteKey:
        """Return the grouping key for one proxy route/status signal."""

        assert event.status_code is not None
        return (
            event.host,
            event.method,
            event.path,
            event.status_code,
        )

    @staticmethod
    def _record_proxy_route_candidate(
        route_candidates: dict[ProxyRouteKey, ProxyRouteCandidate],
        *,
        route_key: ProxyRouteKey,
        is_upstream_error: bool,
        candidate_limit: int,
    ) -> bool:
        """Track likely top route groups while keeping candidate memory bounded."""

        candidate = route_candidates.get(route_key)
        if candidate is not None:
            candidate.estimated_count += 1
            candidate.is_upstream_error = candidate.is_upstream_error or is_upstream_error
            return False
        if len(route_candidates) < candidate_limit:
            route_candidates[route_key] = ProxyRouteCandidate(
                estimated_count=1,
                is_upstream_error=is_upstream_error,
            )
            return False

        eviction_key, evicted = min(
            route_candidates.items(),
            key=lambda item: (
                item[1].estimated_count,
                item[1].is_upstream_error,
            ),
        )
        del route_candidates[eviction_key]
        route_candidates[route_key] = ProxyRouteCandidate(
            estimated_count=evicted.estimated_count + 1,
            is_upstream_error=is_upstream_error,
        )
        return True

    def _build_proxy_route_groups_for_candidates(
        self,
        *,
        sources: list[CollectLogsSourceOut],
        route_keys: set[ProxyRouteKey],
    ) -> dict[ProxyRouteKey, ProxyRouteAccumulator]:
        """Re-scan snapshot sources to compute exact payloads for retained candidates."""

        route_groups: dict[ProxyRouteKey, ProxyRouteAccumulator] = {}
        for event in self._iter_proxy_events(sources):
            if event is None or event.status_code is None or event.status_class is None:
                continue
            status_code = event.status_code
            status_class = event.status_class
            route_key = self._proxy_route_key(event)
            if route_key not in route_keys:
                continue
            route_group = route_groups.get(route_key)
            if route_group is None:
                route_group = ProxyRouteAccumulator(
                    path=event.path,
                    host=event.host,
                    method=event.method,
                    status_code=status_code,
                    status_class=status_class,
                )
                route_groups[route_key] = route_group
            route_group.add(event)
        return route_groups

    @staticmethod
    def _add_sensitive_probe_event(
        event: ProxyLineEvent,
        records: dict[str, ProbeBlockingRecord],
    ) -> None:
        """Add one proxy event if it is a sensitive 403/404 probe."""

        if event.status_code not in {403, 404}:
            return
        if event.client_ip is None or event.path is None:
            return
        if not any(pattern.match(event.path) for pattern in _PROBE_SUSPICIOUS_PATH_PATTERNS):
            return
        record = records.setdefault(event.client_ip, ProbeBlockingRecord(event.client_ip))
        record.sources.add(event.source_key)
        record.suspicious_access_count += 1
        record.paths.add(event.path)
        if event.timestamp:
            record.last_seen = event.timestamp

    @staticmethod
    def _add_crowdsec_appsec_ban_event(
        line: str,
        records: dict[str, ProbeBlockingRecord],
    ) -> None:
        """Add an appsec/second-probe ban fact correlated by source IP."""

        ban_match = _CROWDSEC_APPSEC_SECOND_PROBE_BAN_PATTERN.search(line)
        if ban_match is None:
            return

        ip = ban_match.group("ip")
        timestamp = ban_match.group("timestamp")
        record = records.setdefault(ip, ProbeBlockingRecord(ip))
        record.sources.add("crowdsec_runtime")
        record.appsec_ban_count += 1
        record.last_appsec_ban_at = timestamp

    @staticmethod
    def _select_proxy_snapshot_files(
        requested_source_keys: list[str] | None,
        *,
        sources: list[CollectLogsSourceOut],
    ) -> list[CollectLogsSourceOut]:
        """Validate requested source keys and return proxy sources for analysis."""

        available_sources = [
            source for source in sources if source.status == "collected" and source.file is not None
        ]
        if requested_source_keys:
            available_source_keys = {item.source_key for item in available_sources}
            unknown_source_keys = sorted(set(requested_source_keys) - available_source_keys)
            if unknown_source_keys:
                raise ValueError(
                    "Requested log snapshot source_keys were not found: "
                    + ", ".join(unknown_source_keys)
                )
            return [item for item in available_sources if item.source_key in requested_source_keys]
        return [item for item in available_sources if LogAnalysisService._is_proxy_source(item)]

    @staticmethod
    def _is_proxy_source(source: CollectLogsSourceOut) -> bool:
        """Return whether one collected source looks proxy/ingress-shaped."""

        source_key = source.source_key.lower()
        normalization_profile = (source.normalization_profile or "").lower()
        return (
            normalization_profile in {"proxy_access", "web_logs"}
            or "proxy" in source_key
            or "nginx" in source_key
            or "traefik" in source_key
        )

    @staticmethod
    def _select_snapshot_files(
        requested_source_keys: list[str] | None,
        *,
        sources: list[CollectLogsSourceOut],
    ) -> list[CollectLogsSourceOut]:
        """Return source files selected for analysis, validating requested keys.

        `None` or an empty list means "all collected files in this snapshot."
        Explicit keys must all exist in DB-backed collected sources; callers get
        one deterministic `ValueError` listing missing keys when they do not.
        """

        available_sources = [
            source for source in sources if source.status == "collected" and source.file is not None
        ]
        if not requested_source_keys:
            return available_sources
        available_source_keys = {item.source_key for item in available_sources}
        unknown_source_keys = sorted(set(requested_source_keys) - available_source_keys)
        if unknown_source_keys:
            raise ValueError(
                "Requested log snapshot source_keys were not found: "
                + ", ".join(unknown_source_keys)
            )
        return [item for item in available_sources if item.source_key in requested_source_keys]

    @staticmethod
    def _build_source_summaries(
        groups: list[GroupedErrorPayload],
    ) -> list[IncidentSourceSummaryPayload]:
        """Build per-source counts and timestamp bounds from grouped findings.

        A group can reference multiple source keys. Group counts are credited
        to every source involved, while matching-line counts are credited to
        the primary source of the group's first raw line reference. This keeps
        the summary stable and avoids double-counting the same grouped lines
        across every related source.
        """

        source_group_counts: dict[str, int] = defaultdict(int)
        source_line_counts: dict[str, int] = defaultdict(int)
        source_first_timestamps: dict[str, str | None] = {}
        source_last_timestamps: dict[str, str | None] = {}
        for group in groups:
            for source_key in group.source_keys:
                source_group_counts[source_key] += 1
                if source_key not in source_first_timestamps:
                    source_first_timestamps[source_key] = group.first_timestamp
                if group.last_timestamp is not None:
                    source_last_timestamps[source_key] = group.last_timestamp
            primary_source = group.first_seen.source_key
            source_line_counts[primary_source] += group.count

        return [
            IncidentSourceSummaryPayload(
                source_key=source_key,
                grouped_error_count=source_group_counts[source_key],
                matching_line_count=source_line_counts[source_key],
                first_timestamp=source_first_timestamps.get(source_key),
                last_timestamp=source_last_timestamps.get(source_key),
            )
            for source_key in sorted(source_group_counts)
        ]

    def _parse_proxy_line(
        self,
        *,
        source_key: str,
        output_file: str,
        line_number: int,
        raw_line: str,
        payload: dict[str, Any],
    ) -> ProxyLineEvent:
        """Build one proxy event from an already parsed structured log payload."""

        status_code = self._extract_status_code(payload)
        method, path = self._extract_proxy_method_path(payload)
        line, line_truncated = _truncate_line(raw_line)
        return ProxyLineEvent(
            source_key=source_key,
            output_file=output_file,
            line_number=line_number,
            line=line,
            line_truncated=line_truncated,
            timestamp=self._extract_timestamp(payload),
            status_code=status_code,
            status_class=self._status_class(status_code),
            host=self._extract_first_string(
                payload,
                "host",
                "request_host",
                "http_host",
                "server_name",
                "RequestHost",
            ),
            method=method,
            path=path,
            client_ip=self._extract_first_string(
                payload,
                "remote_addr",
                "client_ip",
                "ip",
                "request_ip",
                "ClientHost",
            ),
            user_agent=self._extract_first_string(
                payload,
                "user_agent",
                "http_user_agent",
                "request_user_agent",
            ),
            upstream_attempted=self._extract_upstream_attempt_state(payload),
        )

    @staticmethod
    def _extract_upstream_attempt_state(payload: dict[str, Any]) -> bool | None:
        """Return true, false, or unknown from concrete upstream telemetry."""

        found_explicit_no_attempt = False
        for key in _UPSTREAM_EVIDENCE_FIELDS:
            if key not in payload:
                continue
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text and text not in {"-", "0"}:
                return True
            found_explicit_no_attempt = True
        return False if found_explicit_no_attempt else None

    @staticmethod
    def _extract_proxy_method_path(payload: dict[str, Any]) -> tuple[str | None, str | None]:
        """Extract method/path from supported proxy request fields."""

        request = payload.get(StructuredLogField.REQUEST)
        request_path: str | None = None
        if isinstance(request, str):
            stripped_request = request.strip()
            match = _REQUEST_METHOD_PATH_PATTERN.match(stripped_request)
            if match is not None:
                return match.group("method"), match.group("path")
            request_path = _extract_request_path(stripped_request)
        path = request_path or _extract_request_path(
            str(payload.get(StructuredLogField.REQUEST_PATH) or "")
        )
        if path is None:
            path = _extract_request_path(
                str(payload.get(StructuredLogField.TRAEFIK_REQUEST_PATH) or "")
            )
        if path is None:
            path = _extract_request_path(str(payload.get(StructuredLogField.PATH) or ""))
        method = (
            payload.get(StructuredLogField.METHOD)
            or payload.get(StructuredLogField.REQUEST_METHOD)
            or payload.get(StructuredLogField.TRAEFIK_REQUEST_METHOD)
        )
        if method is None:
            return None, path
        method_token = str(method).strip()
        return method_token or None, path

    @staticmethod
    def _extract_first_string(payload: dict[str, Any], *keys: str) -> str | None:
        """Return the first non-empty string-ish payload value for ordered keys."""

        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    @staticmethod
    def _status_class(
        status_code: int | None,
    ) -> StatusClass | None:
        """Return the HTTP status class for supported proxy status codes."""

        if status_code is None or status_code < 100 or status_code > 599:
            return None
        if status_code < 200:
            return "1xx"
        if status_code < 300:
            return "2xx"
        if status_code < 400:
            return "3xx"
        if status_code < 500:
            return "4xx"
        return "5xx"

    @staticmethod
    def _severity_rank(severity: str) -> int:
        """Return the sort rank used to show higher-severity groups first."""

        if severity == "high":
            return 0
        if severity == "medium":
            return 1
        return 2

    def _classify_line(
        self,
        *,
        source_key: str,
        output_file: str,
        line_number: int,
        raw_line: str,
        is_proxy_source: bool,
    ) -> ErrorEvent | None:
        """Classify one raw line, trying structured JSON before text fallback."""

        parsed = self._parse_json_line(raw_line)
        if parsed is not None:
            upstream_attempted = None
            if is_proxy_source:
                upstream_attempted = self._extract_upstream_attempt_state(parsed)
            return self._classify_structured_line(
                source_key=source_key,
                output_file=output_file,
                line_number=line_number,
                raw_line=raw_line,
                payload=parsed,
                upstream_attempted=upstream_attempted,
            )

        return self._classify_text_line(
            source_key=source_key,
            output_file=output_file,
            line_number=line_number,
            raw_line=raw_line,
        )

    @staticmethod
    def _parse_json_line(raw_line: str) -> dict[str, Any] | None:
        """Parse one raw log line into a JSON object when possible.

        Supported inputs:

        - direct JSON object lines, for example `{"level": "ERROR", ...}`
        - Docker timestamp-prefixed JSON lines, for example
          `2026-04-30T19:12:38Z {"level": "warn", ...}`

        Returns `None` when the line is not parseable JSON or when the parsed
        JSON value is not an object.
        """

        try:
            parsed = json.loads(raw_line)
        except json.JSONDecodeError:
            docker_json_match = _DOCKER_JSON_LINE_PATTERN.match(raw_line)
            if docker_json_match is None:
                return None
            try:
                parsed = json.loads(docker_json_match.group(1))
            except json.JSONDecodeError:
                return None
        return parsed if isinstance(parsed, dict) else None

    def _classify_structured_line(
        self,
        *,
        source_key: str,
        output_file: str,
        line_number: int,
        raw_line: str,
        payload: dict[str, Any],
        upstream_attempted: bool | None,
    ) -> ErrorEvent | None:
        """Classify one parsed JSON log object into an error event.

        This classifier is intentionally field-based. It recognizes only the
        whitelisted structured-log keys declared in `StructuredLogField`, such
        as level/severity, message/error, HTTP status, request path, and
        timestamp fields. Unknown JSON shapes are ignored.

        A structured log object becomes an event when it has an HTTP 4xx/5xx
        status, an ERROR/CRITICAL level, or a warning level with error-like
        message text.
        """

        status_code = self._extract_status_code(payload)
        level = self._extract_level(payload)
        message = self._extract_message(payload)
        request_method, request_path = self._extract_proxy_method_path(payload)
        request_host = self._extract_first_string(
            payload,
            StructuredLogField.HOST,
            StructuredLogField.REQUEST_HOST,
            "http_host",
            "server_name",
            StructuredLogField.TRAEFIK_REQUEST_HOST,
        )
        if request_host is not None:
            request_host = request_host.casefold()
        timestamp = self._extract_timestamp(payload)

        category: str | None = None
        severity: Literal["high", "medium", "low"] | None = None

        if status_code is not None and status_code >= 500:
            category = "http_5xx"
            severity = "high"
        elif status_code is not None and status_code >= 400:
            category = "http_4xx"
            severity = "medium"
        elif level in {"ERROR", "CRITICAL"}:
            category = "application_error"
            severity = "high"
        elif level in {"WARN", "WARNING"} and self._looks_error_like(message):
            category = "warning_signal"
            severity = "medium"

        if category is None or severity is None:
            return None

        http_message_summary = _build_http_message_summary(
            status_code=status_code,
            request_method=request_method,
            request_host=request_host,
            request_path=request_path,
        )
        semantic_summary, semantic_identity_hash = _build_structured_semantic_metadata(
            payload,
            status_code=status_code,
        )
        identity_message = ""
        if message:
            message_summary = _normalize_text(message)
            identity_message = message
            identity_kind: FingerprintIdentityKind = "explicit_message"
        elif http_message_summary:
            message_summary = _normalize_text(http_message_summary)
            identity_kind = "http_summary"
        elif semantic_summary:
            message_summary = semantic_summary
            identity_kind = "structured_semantic"
        else:
            identity_message = _normalize_raw_fallback(raw_line)
            message_summary = identity_message
            identity_kind = "raw_fallback"
        if semantic_summary and identity_kind in {"explicit_message", "http_summary"}:
            message_summary = f"{message_summary} | {semantic_summary}"
        message_summary, _ = _truncate_line(message_summary)
        fingerprint = self._build_fingerprint(
            source_key=source_key,
            category=category,
            status_code=status_code,
            request_path=request_path,
            request_method=request_method,
            request_host=request_host,
            normalized_message=message_summary,
            identity_kind=identity_kind,
            identity_message=identity_message,
            semantic_identity_hash=semantic_identity_hash,
            upstream_attempted=upstream_attempted,
        )
        line, line_truncated = _truncate_line(raw_line)
        return ErrorEvent(
            source_key=source_key,
            output_file=output_file,
            line_number=line_number,
            line=line,
            line_truncated=line_truncated,
            category=category,
            severity=severity,
            fingerprint=fingerprint,
            message_summary=message_summary,
            timestamp=timestamp,
            request_path=request_path,
            request_method=request_method,
            request_host=request_host,
            status_code=status_code,
            level=level,
            has_explicit_message=bool(message),
            identity_kind=identity_kind,
            semantic_summary=semantic_summary,
            semantic_identity_hash=semantic_identity_hash,
            upstream_attempted=upstream_attempted,
        )

    def _classify_text_line(
        self,
        *,
        source_key: str,
        output_file: str,
        line_number: int,
        raw_line: str,
    ) -> ErrorEvent | None:
        """Classify an unstructured line when it contains failure language.

        This is the fallback path after structured JSON parsing either fails or
        does not produce an event. It intentionally uses conservative keyword
        matching so plain-text logs can still contribute obvious errors without
        trying to infer status codes or structured severity.
        """

        if not self._looks_error_like(raw_line):
            return None
        message_basis, timestamp = _extract_plain_text_message(raw_line)
        message_summary, _ = _truncate_line(_normalize_text(message_basis))
        line, line_truncated = _truncate_line(raw_line)
        return ErrorEvent(
            source_key=source_key,
            output_file=output_file,
            line_number=line_number,
            line=line,
            line_truncated=line_truncated,
            category="text_error",
            severity="medium",
            fingerprint=self._build_fingerprint(
                source_key=source_key,
                category="text_error",
                status_code=None,
                request_path=_extract_request_path(raw_line),
                request_method=None,
                request_host=None,
                normalized_message=message_summary,
                identity_kind="plain_text_message",
                identity_message=message_basis,
                semantic_identity_hash="",
                upstream_attempted=None,
            ),
            message_summary=message_summary,
            timestamp=timestamp,
            request_path=_extract_request_path(raw_line),
            request_method=None,
            request_host=None,
            status_code=None,
            level=None,
            has_explicit_message=True,
            identity_kind="plain_text_message",
            semantic_summary="",
            semantic_identity_hash="",
            upstream_attempted=None,
        )

    @staticmethod
    def _looks_error_like(value: str) -> bool:
        """Return whether text contains the small failure vocabulary we group."""

        lowered = value.lower()
        return any(
            marker in lowered
            for marker in (
                "error",
                "exception",
                "traceback",
                "failed",
                "timeout",
                "denied",
            )
        )

    @staticmethod
    def _extract_status_code(payload: dict[str, Any]) -> int | None:
        """Extract one integer HTTP status code from supported structured fields."""

        raw_value = (
            payload.get(StructuredLogField.STATUS_CODE)
            or payload.get(StructuredLogField.STATUS)
            or payload.get(StructuredLogField.DOWNSTREAM_STATUS)
        )
        if raw_value is None:
            return None
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_level(payload: dict[str, Any]) -> str | None:
        """Extract and normalize one supported structured log level."""

        raw_value = payload.get(StructuredLogField.LEVEL) or payload.get(
            StructuredLogField.SEVERITY
        )
        return str(raw_value).upper() if raw_value is not None else None

    @staticmethod
    def _extract_message(payload: dict[str, Any]) -> str:
        """Extract the best supported message-like field from a structured log line."""

        return str(
            payload.get(StructuredLogField.MESSAGE)
            or payload.get(StructuredLogField.ERROR)
            or payload.get(StructuredLogField.MSG)
            or ""
        )

    @staticmethod
    def _extract_timestamp(payload: dict[str, Any]) -> str | None:
        """Extract a timestamp-like value from a structured log line."""

        for structured_field in TIMESTAMP_FIELDS:
            value = payload.get(structured_field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _build_fingerprint(
        *,
        source_key: str,
        category: str,
        status_code: int | None,
        request_path: str | None,
        request_method: str | None,
        request_host: str | None,
        normalized_message: str,
        identity_kind: FingerprintIdentityKind,
        identity_message: str,
        semantic_identity_hash: str,
        upstream_attempted: bool | None,
    ) -> str:
        """Build the stable key used to merge events into groups.

        Structured identity keeps exact method, host, path, explicit message,
        stable semantic fields, and identity origin. The monitoring agent can
        then merge conservative semantic variants without losing distinctions
        here. The compact digest avoids copying long messages into the public
        fingerprint. Message-less access logs omit volatile raw metadata; other
        message-less structured events use whitelisted semantic fields, or a
        separately normalized raw fallback when no such fields exist.
        """

        if (status_code is not None and request_path) or identity_message or semantic_identity_hash:
            canonical_fields: list[object] = [
                category,
                status_code,
                request_method or "",
                request_host or "",
                request_path,
                identity_kind,
                " ".join(identity_message.split()),
                semantic_identity_hash,
            ]
            if upstream_attempted is not None:
                canonical_fields.append(upstream_attempted)
            canonical_identity = json.dumps(
                canonical_fields,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            identity_hash = hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()
            return f"{source_key}:{category}:{GROUP_ERRORS_FINGERPRINT_VERSION}:{identity_hash}"
        return f"{source_key}:{category}:{normalized_message}"

    @staticmethod
    def _build_suggested_next_steps(groups: list[GroupedErrorPayload]) -> list[str]:
        """Return deterministic next-step tips derived from the strongest group."""

        if not groups:
            return ["Use grep_log_snapshot for a narrower pattern if you need targeted inspection."]
        top_group = groups[0]
        return [
            (
                "Read the raw snapshot file around "
                f"{top_group.first_seen.source_key}:{top_group.first_seen.line_number} "
                "for the highest-priority grouped finding."
            ),
            (
                "Use grep_log_snapshot with a narrower pattern if one grouped "
                "error needs deeper drilling."
            ),
        ]
