"""Deterministic noise filtering for persisted log snapshots."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from database.fields import FileReference
from database.schemas import CollectLogsSourceOut
from services.log_snapshots import LogSnapshotService
from tools.models import (
    CreateFilteredViewPayload,
    FilteredViewMode,
    FilteredViewSourceSummaryPayload,
    LogSnapshotMetadata,
    SnapshotLineReferencePayload,
)

MAX_FILTERED_LINE_BYTES = 2000
HTTP_NOT_MODIFIED_STATUS = 304
_HEALTH_PATHS = {"/health", "/healthz", "/ping", "/ready", "/readyz", "/live", "/livez"}
_DOCKER_JSON_LINE_PATTERN = re.compile(r"^\S+\s+({.*})\s*$")
_COMBINED_LOG_STATUS_PATTERN = re.compile(r'"\S+\s+\S+\s+HTTP/[^"]+"\s+(\d{3})\b')
_INCIDENT_KEYWORDS = (
    "error",
    "exception",
    "traceback",
    "failed",
    "failure",
    "critical",
    "fatal",
    "panic",
    "banned",
    "blocked",
    "forbidden",
)


def _snapshot_dir_from_metadata(metadata: LogSnapshotMetadata) -> str:
    """Return the relative snapshot directory represented by snapshot metadata."""

    if not metadata.files:
        return ""
    return Path(metadata.files[0].output_file).parent.as_posix()


def _successful_health_status(status_code: int | None) -> bool:
    """Return whether a health request has an explicit successful status."""

    return status_code is not None and 200 <= status_code < 300


def is_successful_health_request(
    request_path: str | None,
    status_code: int | None,
) -> bool:
    """Return whether one known health path has an explicit 2xx status."""

    return request_path in _HEALTH_PATHS and _successful_health_status(status_code)


def _successful_static_asset_status(status_code: int | None) -> bool:
    """Return whether MCP can safely treat an asset request as routine noise."""

    if status_code is None:
        return False
    return status_code < 300 or status_code == HTTP_NOT_MODIFIED_STATUS


def _request_asset_path(request_path: str) -> str:
    """Normalize a request path before comparing it with manifest asset rules."""

    return request_path.split("?", 1)[0].split("#", 1)[0]


def _is_static_asset_request(context: SourceNoiseContext, request_path: str) -> bool:
    """Return whether the request path matches project-declared static assets."""

    asset_path = _request_asset_path(request_path)
    if asset_path in context.static_asset_paths:
        return True
    lower_asset_path = asset_path.lower()
    return any(
        lower_asset_path.endswith(extension.lower())
        for extension in context.static_asset_extensions
    )


def _is_successful_static_asset_request(
    context: SourceNoiseContext,
    request_path: str,
    status_code: int | None,
) -> bool:
    """Return whether a static asset request is successful enough to filter out."""

    return _is_static_asset_request(
        context,
        request_path,
    ) and _successful_static_asset_status(status_code)


@dataclass(frozen=True, slots=True)
class SourceNoiseContext:
    """Noise-cleaning routing metadata for one persisted source.

    The fields come from manifest/source metadata and are used only to choose
    deterministic filtering rules. They are intentionally not returned in the
    public filtered-view response because they are implementation details.
    """

    source_key: str
    parser_type: str | None
    normalization_profile: str | None
    default_noise_profile: str | None
    static_asset_paths: tuple[str, ...] = ()
    static_asset_extensions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FilterDecision:
    """Describe whether one line is kept or excluded by deterministic rules."""

    keep: bool
    reason: str | None = None


@dataclass(slots=True)
class SourceFilteredSummary:
    """Mutable per-source counters before conversion into response summaries."""

    context: SourceNoiseContext
    total_line_count: int = 0
    kept_line_count: int = 0
    excluded_line_count: int = 0
    exclusion_reasons: Counter[str] | None = None

    def __post_init__(self) -> None:
        self.exclusion_reasons = Counter()


@dataclass(slots=True)
class FilteredLineCandidates:
    """Bounded candidate line storage for non-chronological filtered views."""

    head_lines: list[SnapshotLineReferencePayload]
    incident_lines: list[SnapshotLineReferencePayload]
    regular_lines: list[SnapshotLineReferencePayload]
    sample_lines_by_source: dict[str, list[SnapshotLineReferencePayload]]


class CreateFilteredViewError(BaseModel):
    """Service-level error returned when a filtered view cannot be built."""

    message: str
    error_code: str
    retry_tips: list[str]


class LogFilteringService:
    """Generate deterministic cleaned views from persisted raw snapshots.

    This service reads already persisted raw log files and applies deterministic
    filters to remove low-signal lines such as successful health checks and
    successful static asset requests. It never mutates the raw snapshot; the
    returned payload is only a derived analysis view.
    """

    def __init__(self, snapshot_service: LogSnapshotService | None = None) -> None:
        self.snapshot_service = snapshot_service or LogSnapshotService()

    def create_filtered_view(
        self,
        metadata: LogSnapshotMetadata,
        *,
        sources: list[CollectLogsSourceOut],
        source_contexts: dict[str, SourceNoiseContext],
        source_keys: list[str] | None,
        max_lines: int,
        requested_project_name: str | None,
        project_name: str,
        view_mode: FilteredViewMode,
        next_step_tips: list[str],
    ) -> CreateFilteredViewPayload | CreateFilteredViewError:
        """Create one deterministic cleaned view from persisted snapshot metadata.

        The caller supplies source contexts from the project manifest. This
        method validates requested source keys, reads selected persisted files,
        applies source-specific noise rules, and returns bounded kept lines
        plus aggregate counts. Unknown source keys are returned as
        `CreateFilteredViewError` instead of raised so the tool layer can map
        them to the normal MCP error shape.
        """

        available_sources = [
            source for source in sources if source.status == "collected" and source.file is not None
        ]
        available_source_keys = {item.source_key for item in available_sources}

        if source_keys:
            unknown_source_keys = sorted(set(source_keys) - available_source_keys)
            if unknown_source_keys:
                return CreateFilteredViewError(
                    message=(
                        "Requested log snapshot source_keys were not found: "
                        + ", ".join(unknown_source_keys)
                    ),
                    error_code="snapshot_source_key_not_found",
                    retry_tips=[
                        (
                            "Retry with a valid archive_name and source_keys "
                            "for the authorized project."
                        ),
                    ],
                )

        selected_items = [
            item
            for item in available_sources
            if source_keys is None or item.source_key in source_keys
        ]
        searched_source_keys = [item.source_key for item in selected_items]
        candidates = FilteredLineCandidates(
            head_lines=[],
            incident_lines=[],
            regular_lines=[],
            sample_lines_by_source={},
        )
        source_summaries: dict[str, SourceFilteredSummary] = {}
        total_line_count = 0
        kept_line_count = 0
        excluded_line_count = 0

        for item in selected_items:
            context = source_contexts.get(
                item.source_key,
                SourceNoiseContext(
                    source_key=item.source_key,
                    parser_type=item.parser_type,
                    normalization_profile=item.normalization_profile,
                    default_noise_profile=item.default_noise_profile,
                ),
            )
            summary = source_summaries.setdefault(item.source_key, SourceFilteredSummary(context))
            file_ref = cast(FileReference, item.file)
            try:
                with open(file_ref.path, encoding="utf-8", errors="replace") as file:
                    for line_number, raw_line in enumerate(file, start=1):
                        line = raw_line.rstrip("\n")
                        total_line_count += 1
                        summary.total_line_count += 1
                        decision = self._apply_noise_profile(context, line)
                        truncated_line, line_truncated = self._truncate_line(line)
                        if decision.keep:
                            kept_line_count += 1
                            summary.kept_line_count += 1
                            line_reference = SnapshotLineReferencePayload(
                                source_key=item.source_key,
                                output_file=file_ref.name,
                                line_number=line_number,
                                line=truncated_line,
                                line_truncated=line_truncated,
                            )
                            self._store_filtered_line_candidate(
                                candidates=candidates,
                                source_key=item.source_key,
                                line=line,
                                line_reference=line_reference,
                                max_lines=max_lines,
                                view_mode=view_mode,
                            )
                        else:
                            excluded_line_count += 1
                            summary.excluded_line_count += 1
                            assert summary.exclusion_reasons is not None
                            summary.exclusion_reasons[decision.reason or "excluded_by_profile"] += 1
            except ValueError:
                return CreateFilteredViewError(
                    message="Requested persisted source file reference is invalid.",
                    error_code="invalid_source_file_reference",
                    retry_tips=[
                        "Run collect_logs again to recreate source files for this project.",
                    ],
                )
            except OSError:
                return CreateFilteredViewError(
                    message="Requested log snapshot file was not found on disk.",
                    error_code="snapshot_file_not_found",
                    retry_tips=[
                        "Run collect_logs again to recreate the missing persisted file.",
                    ],
                )

        cleaned_lines = self._select_filtered_lines(
            candidates=candidates,
            selected_source_keys=searched_source_keys,
            max_lines=max_lines,
            view_mode=view_mode,
        )
        payload_source_summaries = [
            FilteredViewSourceSummaryPayload(
                source_key=source_key,
                total_line_count=summary.total_line_count,
                kept_line_count=summary.kept_line_count,
                excluded_line_count=summary.excluded_line_count,
                top_exclusion_reasons=[
                    reason
                    for reason, _count in (summary.exclusion_reasons or Counter()).most_common(3)
                ],
            )
            for source_key, summary in sorted(source_summaries.items())
        ]

        return CreateFilteredViewPayload(
            action="create_filtered_view",
            requested_project_name=requested_project_name,
            project_name=project_name,
            workspace=metadata.workspace,
            session_id=metadata.session_id,
            snapshot_dir=_snapshot_dir_from_metadata(metadata),
            searched_source_keys=searched_source_keys,
            view_mode=view_mode,
            max_lines=max_lines,
            total_line_count=total_line_count,
            kept_line_count=kept_line_count,
            excluded_line_count=excluded_line_count,
            returned_line_count=len(cleaned_lines),
            next_step_tips=next_step_tips,
            truncated=kept_line_count > max_lines,
            cleaned_lines=cleaned_lines,
            source_summaries=payload_source_summaries,
        )

    def _store_filtered_line_candidate(
        self,
        *,
        candidates: FilteredLineCandidates,
        source_key: str,
        line: str,
        line_reference: SnapshotLineReferencePayload,
        max_lines: int,
        view_mode: FilteredViewMode,
    ) -> None:
        """Store only the candidate lines needed for the requested view mode."""

        if view_mode == "head":
            if len(candidates.head_lines) < max_lines:
                candidates.head_lines.append(line_reference)
            return

        if view_mode == "errors":
            if self._is_incident_line(line):
                if len(candidates.incident_lines) < max_lines:
                    candidates.incident_lines.append(line_reference)
            elif len(candidates.regular_lines) < max_lines:
                candidates.regular_lines.append(line_reference)
            return

        source_lines = candidates.sample_lines_by_source.setdefault(source_key, [])
        if len(source_lines) < max_lines:
            source_lines.append(line_reference)

    @staticmethod
    def _select_filtered_lines(
        *,
        candidates: FilteredLineCandidates,
        selected_source_keys: list[str],
        max_lines: int,
        view_mode: FilteredViewMode,
    ) -> list[SnapshotLineReferencePayload]:
        """Return the final bounded filtered line list for one response mode."""

        if view_mode == "head":
            return candidates.head_lines[:max_lines]

        if view_mode == "errors":
            return (candidates.incident_lines + candidates.regular_lines)[:max_lines]

        sampled_lines: list[SnapshotLineReferencePayload] = []
        next_index = 0
        while len(sampled_lines) < max_lines:
            added_line = False
            for source_key in selected_source_keys:
                source_lines = candidates.sample_lines_by_source.get(source_key, [])
                if next_index < len(source_lines):
                    sampled_lines.append(source_lines[next_index])
                    added_line = True
                    if len(sampled_lines) >= max_lines:
                        break
            if not added_line:
                break
            next_index += 1
        return sampled_lines

    def _apply_noise_profile(self, context: SourceNoiseContext, raw_line: str) -> FilterDecision:
        """Apply the manifest-selected noise profile to one raw line."""

        profile_name = context.default_noise_profile
        if not profile_name:
            return FilterDecision(keep=True)

        parsed = self._parse_json_line(raw_line)
        request_path = self._extract_request_path(parsed, raw_line)
        status_code = self._extract_status_code(parsed, raw_line)
        if profile_name == "web_noise":
            return self._filter_web_noise(context, request_path, status_code)
        if profile_name == "proxy_noise":
            return self._filter_proxy_noise(context, request_path, status_code)
        if profile_name == "backend_noise":
            return self._filter_backend_noise(request_path, status_code)
        if profile_name == "frontend_noise":
            return self._filter_frontend_noise(context, request_path, status_code)
        return FilterDecision(keep=True)

    @staticmethod
    def _filter_web_noise(
        context: SourceNoiseContext,
        request_path: str | None,
        status_code: int | None,
    ) -> FilterDecision:
        if request_path is None:
            return FilterDecision(keep=True)
        if is_successful_health_request(request_path, status_code):
            return FilterDecision(keep=False, reason="health_check_request")
        if _is_successful_static_asset_request(context, request_path, status_code):
            return FilterDecision(keep=False, reason="successful_static_asset_request")
        return FilterDecision(keep=True)

    @staticmethod
    def _filter_proxy_noise(
        context: SourceNoiseContext,
        request_path: str | None,
        status_code: int | None,
    ) -> FilterDecision:
        if request_path is None:
            return FilterDecision(keep=True)
        if is_successful_health_request(request_path, status_code):
            return FilterDecision(keep=False, reason="proxy_health_check_request")
        if request_path.startswith("/.well-known/acme-challenge/") and (
            status_code is None or status_code < 500
        ):
            return FilterDecision(keep=False, reason="certificate_challenge_request")
        if _is_successful_static_asset_request(context, request_path, status_code):
            return FilterDecision(keep=False, reason="successful_static_asset_request")
        return FilterDecision(keep=True)

    @staticmethod
    def _filter_backend_noise(
        request_path: str | None,
        status_code: int | None,
    ) -> FilterDecision:
        if request_path in _HEALTH_PATHS:
            if is_successful_health_request(request_path, status_code):
                return FilterDecision(keep=False, reason="application_health_check_log")
            return FilterDecision(keep=True)
        return FilterDecision(keep=True)

    @staticmethod
    def _filter_frontend_noise(
        context: SourceNoiseContext,
        request_path: str | None,
        status_code: int | None,
    ) -> FilterDecision:
        if request_path in _HEALTH_PATHS:
            if is_successful_health_request(request_path, status_code):
                return FilterDecision(keep=False, reason="frontend_health_check_log")
            return FilterDecision(keep=True)
        if request_path and _is_successful_static_asset_request(
            context,
            request_path,
            status_code,
        ):
            return FilterDecision(keep=False, reason="successful_static_asset_request")
        return FilterDecision(keep=True)

    @staticmethod
    def _parse_json_line(raw_line: str) -> dict[str, Any] | None:
        """Parse plain or Docker timestamp-prefixed JSON log lines."""

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

    @staticmethod
    def _extract_request_path(parsed: dict[str, Any] | None, raw_line: str) -> str | None:
        if parsed is not None:
            for key in ("request_path", "path", "url_path", "uri", "RequestPath"):
                value = parsed.get(key)
                if isinstance(value, str) and value.startswith("/"):
                    return value
            request_value = parsed.get("request")
            if isinstance(request_value, str):
                request_parts = request_value.split()
                if len(request_parts) >= 2 and request_parts[1].startswith("/"):
                    return request_parts[1]
        request_parts = raw_line.split()
        if len(request_parts) >= 2 and request_parts[1].startswith("/"):
            return request_parts[1]
        return None

    @staticmethod
    def _extract_status_code(
        parsed: dict[str, Any] | None,
        raw_line: str | None = None,
    ) -> int | None:
        if parsed is not None:
            for key in (
                "status",
                "status_code",
                "statusCode",
                "DownstreamStatus",
                "OriginStatus",
                "downstream_status",
                "origin_status",
            ):
                value = parsed.get(key)
                if isinstance(value, int):
                    return value
                if isinstance(value, str) and value.isdigit():
                    return int(value)
        if raw_line is not None:
            status_match = _COMBINED_LOG_STATUS_PATTERN.search(raw_line)
            if status_match is not None:
                return int(status_match.group(1))
        return None

    @staticmethod
    def _extract_level(parsed: dict[str, Any] | None) -> str | None:
        if parsed is None:
            return None
        value = parsed.get("level")
        if isinstance(value, str):
            return value.strip().lower()
        return None

    def _is_incident_line(self, raw_line: str) -> bool:
        """Return whether one kept line is likely useful for incident-first review."""

        parsed = self._parse_json_line(raw_line)
        status_code = self._extract_status_code(parsed, raw_line)
        if status_code is not None and status_code >= 400:
            return True

        level = self._extract_level(parsed)
        if level in {"warning", "warn", "error", "critical", "exception", "fatal"}:
            return True

        lowered_line = raw_line.lower()
        return any(keyword in lowered_line for keyword in _INCIDENT_KEYWORDS)

    @staticmethod
    def _truncate_line(value: str) -> tuple[str, bool]:
        encoded = value.encode("utf-8")
        if len(encoded) <= MAX_FILTERED_LINE_BYTES:
            return value, False
        return encoded[:MAX_FILTERED_LINE_BYTES].decode("utf-8", errors="ignore"), True
