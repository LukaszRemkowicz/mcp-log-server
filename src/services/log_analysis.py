"""Deterministic analysis over persisted log snapshots."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from tools.models import (
    GroupedErrorPayload,
    IncidentBundlePayload,
    IncidentSourceSummaryPayload,
    LogSnapshotMetadata,
    SnapshotLineReferencePayload,
)
from utils.log_snapshots import resolve_snapshot_file_path

MAX_ANALYSIS_LINE_BYTES = 2000

_UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_LONG_NUMBER_PATTERN = re.compile(r"\b\d{2,}\b")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_REQUEST_LINE_PATTERN = re.compile(r"^[A-Z]+\s+(\S+)\s+HTTP/\d(?:\.\d)?$")


def _normalize_text(value: str) -> str:
    """Normalize noisy identifiers so similar messages group together."""

    value = _UUID_PATTERN.sub("<uuid>", value)
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
    """Extract a request path from direct path fields or HTTP request lines."""

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


@dataclass(slots=True)
class ErrorEvent:
    """One deterministic error-like event extracted from a snapshot line."""

    source_key: str
    output_file: str
    line_number: int
    line: str
    line_truncated: bool
    category: str
    severity: Literal["high", "medium", "low"]
    fingerprint: str
    message: str
    timestamp: str | None
    request_path: str | None
    status_code: int | None
    level: str | None


@dataclass(slots=True)
class ErrorGroupAccumulator:
    """Mutable grouping state before conversion into a payload model."""

    fingerprint: str
    category: str
    severity: Literal["high", "medium", "low"]
    sample_message: str
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
        """Merge one event into the group."""

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
        """Convert the accumulated group into the public MCP payload."""

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
            sample_message=self.sample_message,
            first_timestamp=self.first_timestamp,
            last_timestamp=self.last_timestamp,
            first_seen=self.first_seen,
            last_seen=self.last_seen,
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

    def group_snapshot_errors(
        self,
        snapshot_dir: Path,
        metadata: LogSnapshotMetadata,
        *,
        source_keys: list[str] | None,
        max_groups: int,
    ) -> tuple[list[GroupedErrorPayload], int, list[str], int]:
        """Group repeated error-like lines from selected snapshot files."""

        if source_keys:
            available_source_keys = {item.source_key for item in metadata.files}
            unknown_source_keys = sorted(set(source_keys) - available_source_keys)
            if unknown_source_keys:
                raise ValueError(
                    "Requested log snapshot source_keys were not found: "
                    + ", ".join(unknown_source_keys)
                )

        selected_files = [
            item for item in metadata.files if source_keys is None or item.source_key in source_keys
        ]
        groups: dict[str, ErrorGroupAccumulator] = {}
        matching_line_count = 0

        for item in selected_files:
            output_path = resolve_snapshot_file_path(snapshot_dir, item)
            with output_path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    event = self._classify_line(
                        source_key=item.source_key,
                        output_file=str(output_path),
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
                            sample_message=event.message,
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
        return returned_groups, matching_line_count, searched_source_keys, len(sorted_groups)

    def build_incident_bundle(
        self,
        snapshot_dir: Path,
        metadata: LogSnapshotMetadata,
        *,
        source_keys: list[str] | None,
        max_groups: int,
        requested_project_name: str | None,
        authorized_project_name: str,
        effective_project_name: str,
        analysis_cautions: list[str],
        next_step_tips: list[str],
    ) -> IncidentBundlePayload:
        """Build one compact incident bundle from deterministic grouped findings."""

        groups, matching_line_count, searched_source_keys, total_group_count = (
            self.group_snapshot_errors(
                snapshot_dir,
                metadata,
                source_keys=source_keys,
                max_groups=max_groups,
            )
        )
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

        source_summaries = [
            IncidentSourceSummaryPayload(
                source_key=source_key,
                grouped_error_count=source_group_counts[source_key],
                matching_line_count=source_line_counts[source_key],
                first_timestamp=source_first_timestamps.get(source_key),
                last_timestamp=source_last_timestamps.get(source_key),
            )
            for source_key in sorted(source_group_counts)
        ]

        high_count = sum(1 for group in groups if group.severity == "high")
        medium_count = sum(1 for group in groups if group.severity == "medium")
        low_count = sum(1 for group in groups if group.severity == "low")

        suggested_next_steps = self._build_suggested_next_steps(groups)
        return IncidentBundlePayload(
            action="build_incident_bundle",
            requested_project_name=requested_project_name,
            authorized_project_name=authorized_project_name,
            effective_project_name=effective_project_name,
            workspace=metadata.workspace,
            snapshot_id=metadata.snapshot_id,
            snapshot_dir=str(snapshot_dir),
            searched_source_keys=searched_source_keys,
            analysis_cautions=analysis_cautions,
            next_step_tips=next_step_tips,
            grouped_error_count=total_group_count,
            matching_line_count=matching_line_count,
            high_severity_group_count=high_count,
            medium_severity_group_count=medium_count,
            low_severity_group_count=low_count,
            top_groups=groups,
            source_summaries=source_summaries,
            suggested_next_steps=suggested_next_steps,
        )

    @staticmethod
    def _severity_rank(severity: str) -> int:
        """Sort higher-severity groups first."""

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
        """Turn one raw log line into a deterministic error event when relevant."""

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
        """Parse one line as JSON when possible."""

        try:
            parsed = json.loads(raw_line)
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
        """Classify one JSON-like line into a grouped error event."""

        status_code = self._extract_status_code(payload)
        level = str(payload.get("level") or payload.get("severity") or "").upper() or None
        message = str(payload.get("message") or payload.get("error") or payload.get("msg") or "")
        request_path = (
            _extract_request_path(str(payload.get("request_path") or ""))
            or _extract_request_path(str(payload.get("path") or ""))
            or _extract_request_path(str(payload.get("request") or ""))
        )
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
        normalized_message = _normalize_text(message_basis)
        fingerprint = self._build_fingerprint(
            source_key=source_key,
            category=category,
            status_code=status_code,
            request_path=request_path,
            normalized_message=normalized_message,
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
            message=message_basis,
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
        """Classify one unstructured line when it still looks error-like."""

        if not self._looks_error_like(raw_line):
            return None
        normalized_message = _normalize_text(raw_line)
        line, line_truncated = _truncate_line(raw_line)
        return ErrorEvent(
            source_key=source_key,
            output_file=output_file,
            line_number=line_number,
            line=line,
            line_truncated=line_truncated,
            category="text_error",
            severity="medium",
            fingerprint=f"{source_key}:text_error:{normalized_message}",
            message=raw_line,
            timestamp=None,
            request_path=_extract_request_path(raw_line),
            status_code=None,
            level=None,
        )

    @staticmethod
    def _looks_error_like(value: str) -> bool:
        """Return whether one message/line carries obvious failure semantics."""

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
        """Extract one integer HTTP status code from a structured log line."""

        raw_value = payload.get("status_code", payload.get("status"))
        if raw_value is None:
            return None
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_timestamp(payload: dict[str, Any]) -> str | None:
        """Extract a timestamp-like value from a structured log line."""

        for key in ("timestamp", "time", "time_local", "ts"):
            value = payload.get(key)
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
        """Build one stable fingerprint for deterministic grouping."""

        if status_code is not None and request_path:
            return f"{source_key}:{category}:{status_code}:{request_path}"
        return f"{source_key}:{category}:{normalized_message}"

    @staticmethod
    def _build_suggested_next_steps(groups: list[GroupedErrorPayload]) -> list[str]:
        """Return deterministic follow-up suggestions for the incident bundle."""

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
