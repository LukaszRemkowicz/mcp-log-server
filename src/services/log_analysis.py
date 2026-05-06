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

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from tools.models import (
    GroupedErrorPayload,
    IncidentBundlePayload,
    IncidentSourceSummaryPayload,
    LogSnapshotFilePayload,
    LogSnapshotMetadata,
    SnapshotLineReferencePayload,
)
from utils.log_snapshots import resolve_snapshot_file_path

MAX_ANALYSIS_LINE_BYTES = 2000

_UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_LONG_HEX_PATTERN = re.compile(r"\b[0-9a-fA-F]{12,}\b")
_LONG_NUMBER_PATTERN = re.compile(r"\b\d{2,}\b")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_REQUEST_LINE_PATTERN = re.compile(r"^[A-Z]+\s+(\S+)\s+HTTP/\d(?:\.\d)?$")
_DOCKER_JSON_LINE_PATTERN = re.compile(r"^\S+\s+({.*})\s*$")


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
    REQUEST_PATH = "request_path"
    PATH = "path"
    REQUEST = "request"
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


def _normalize_text(value: str) -> str:
    """Normalize noisy identifiers so similar messages group together.

    UUIDs, long hashes, long numeric ids, and repeated whitespace are replaced
    before fingerprinting. This prevents equivalent failures from becoming
    separate groups only because they contain request ids, container ids, or
    generated object ids.
    """

    value = _UUID_PATTERN.sub("<uuid>", value)
    value = _LONG_HEX_PATTERN.sub("<id>", value)
    value = _LONG_NUMBER_PATTERN.sub("<n>", value)
    value = _WHITESPACE_PATTERN.sub(" ", value.strip())
    return value


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


def _snapshot_dir_from_metadata(metadata: LogSnapshotMetadata) -> str:
    """Return the relative snapshot directory represented by metadata files."""

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
    status_code: int | None
    level: str | None


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
    count: int = 0
    source_keys: set[str] = field(default_factory=set)
    request_paths: set[str] = field(default_factory=set)
    status_codes: set[int] = field(default_factory=set)
    levels: set[str] = field(default_factory=set)
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
        if event.status_code is not None:
            self.status_codes.add(event.status_code)
        if event.level:
            self.levels.add(event.level)
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
            status_codes=sorted(self.status_codes),
            levels=sorted(self.levels),
            message_summary=self.message_summary,
            first_timestamp=self.first_timestamp,
            last_timestamp=self.last_timestamp,
            first_seen=self.first_seen,
            last_seen=self.last_seen,
        )


@dataclass(frozen=True, slots=True)
class GroupedSnapshotAnalysis:
    """Grouped-error analysis result before tool-specific response shaping.

    Keeping this as a named object avoids positional tuple unpacking in callers
    and makes the four result values explicit:

    - returned groups after `max_groups` truncation
    - total matching error-like line count
    - source keys that were actually searched
    - total group count before truncation
    """

    groups: list[GroupedErrorPayload]
    matching_line_count: int
    searched_source_keys: list[str]
    total_group_count: int


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

    def group_snapshot_errors(
        self,
        metadata: LogSnapshotMetadata,
        *,
        source_keys: list[str] | None,
        max_groups: int,
    ) -> GroupedSnapshotAnalysis:
        """Group repeated error-like lines from selected persisted source files.

        The caller passes already-loaded snapshot metadata. This method resolves
        the selected source files from their relative metadata paths, classifies
        each line, groups matching events by deterministic fingerprint, and
        returns both the truncated groups and the untruncated group count.

        Raises:
            ValueError: When `source_keys` contains a key that is not present in
                the snapshot metadata, or when one persisted source file cannot
                be safely resolved on disk.
        """

        selected_files = self._select_snapshot_files(metadata, source_keys)
        groups: dict[str, ErrorGroupAccumulator] = {}
        matching_line_count = 0

        for item in selected_files:
            output_path = resolve_snapshot_file_path(item)
            with output_path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    event = self._classify_line(
                        source_key=item.source_key,
                        output_file=item.output_file,
                        line_number=line_number,
                        raw_line=raw_line.rstrip("\n"),
                    )
                    if event is None:
                        continue
                    matching_line_count += 1
                    group = groups.get(event.fingerprint)
                    if group is None:
                        group = ErrorGroupAccumulator(
                            fingerprint=event.fingerprint,
                            category=event.category,
                            severity=event.severity,
                            message_summary=event.message_summary,
                        )
                        groups[event.fingerprint] = group
                    group.add(event)

        sorted_groups = sorted(
            (group.to_payload() for group in groups.values()),
            key=lambda payload: (
                self._severity_rank(payload.severity),
                -payload.count,
                payload.fingerprint,
            ),
        )
        returned_groups = sorted_groups[:max_groups]
        searched_source_keys = [item.source_key for item in selected_files]
        return GroupedSnapshotAnalysis(
            groups=returned_groups,
            matching_line_count=matching_line_count,
            searched_source_keys=searched_source_keys,
            total_group_count=len(sorted_groups),
        )

    def build_incident_bundle(
        self,
        metadata: LogSnapshotMetadata,
        *,
        source_keys: list[str] | None,
        max_groups: int,
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
            metadata,
            source_keys=source_keys,
            max_groups=max_groups,
        )
        high_count = sum(1 for group in analysis.groups if group.severity == "high")
        medium_count = sum(1 for group in analysis.groups if group.severity == "medium")
        low_count = sum(1 for group in analysis.groups if group.severity == "low")

        suggested_next_steps = self._build_suggested_next_steps(analysis.groups)
        return IncidentBundlePayload(
            action="build_incident_bundle",
            requested_project_name=requested_project_name,
            project_name=project_name,
            workspace=metadata.workspace,
            session_id=metadata.session_id,
            snapshot_dir=_snapshot_dir_from_metadata(metadata),
            searched_source_keys=analysis.searched_source_keys,
            analysis_cautions=analysis_cautions,
            next_step_tips=next_step_tips,
            grouped_error_count=analysis.total_group_count,
            matching_line_count=analysis.matching_line_count,
            high_severity_group_count=high_count,
            medium_severity_group_count=medium_count,
            low_severity_group_count=low_count,
            top_groups=analysis.groups,
            source_summaries=self._build_source_summaries(analysis.groups),
            suggested_next_steps=suggested_next_steps,
        )

    @staticmethod
    def _select_snapshot_files(
        metadata: LogSnapshotMetadata,
        source_keys: list[str] | None,
    ) -> list[LogSnapshotFilePayload]:
        """Return source files selected for analysis, validating requested keys.

        `None` or an empty list means "all files in this snapshot." Explicit
        keys must all exist in metadata; callers get one deterministic
        `ValueError` listing the missing keys when they do not.
        """

        if not source_keys:
            return list(metadata.files)

        available_source_keys = {item.source_key for item in metadata.files}
        unknown_source_keys = sorted(set(source_keys) - available_source_keys)
        if unknown_source_keys:
            raise ValueError(
                "Requested log snapshot source_keys were not found: "
                + ", ".join(unknown_source_keys)
            )
        return [item for item in metadata.files if item.source_key in source_keys]

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
    ) -> ErrorEvent | None:
        """Classify one raw line, trying structured JSON before text fallback."""

        parsed = self._parse_json_line(raw_line)
        if parsed is not None:
            event = self._classify_structured_line(
                source_key=source_key,
                output_file=output_file,
                line_number=line_number,
                raw_line=raw_line,
                payload=parsed,
            )
            if event is not None:
                return event

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
        request_path = self._extract_structured_request_path(payload)
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

        message_basis = message or raw_line
        message_summary = _normalize_text(message_basis)
        fingerprint = self._build_fingerprint(
            source_key=source_key,
            category=category,
            status_code=status_code,
            request_path=request_path,
            normalized_message=message_summary,
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
            status_code=status_code,
            level=level,
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
        message_summary = _normalize_text(raw_line)
        line, line_truncated = _truncate_line(raw_line)
        return ErrorEvent(
            source_key=source_key,
            output_file=output_file,
            line_number=line_number,
            line=line,
            line_truncated=line_truncated,
            category="text_error",
            severity="medium",
            fingerprint=f"{source_key}:text_error:{message_summary}",
            message_summary=message_summary,
            timestamp=None,
            request_path=_extract_request_path(raw_line),
            status_code=None,
            level=None,
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

        raw_value = payload.get(StructuredLogField.STATUS_CODE) or payload.get(
            StructuredLogField.STATUS
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
    def _extract_structured_request_path(payload: dict[str, Any]) -> str | None:
        """Extract a request path from supported structured request fields."""

        return (
            _extract_request_path(str(payload.get(StructuredLogField.REQUEST_PATH) or ""))
            or _extract_request_path(str(payload.get(StructuredLogField.PATH) or ""))
            or _extract_request_path(str(payload.get(StructuredLogField.REQUEST) or ""))
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
        normalized_message: str,
    ) -> str:
        """Build the stable key used to merge events into groups.

        HTTP events prefer source/category/status/path so repeated requests to
        the same failing endpoint group together even when messages vary.
        Non-HTTP events use the normalized message summary.
        """

        if status_code is not None and request_path:
            return f"{source_key}:{category}:{status_code}:{request_path}"
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
