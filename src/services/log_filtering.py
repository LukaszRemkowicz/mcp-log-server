"""Deterministic noise filtering for persisted log snapshots."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from tools.models import (
    CreateFilteredViewPayload,
    FilteredViewSourceSummaryPayload,
    LogSnapshotMetadata,
    SnapshotLineReferencePayload,
)
from utils.log_snapshots import resolve_snapshot_file_path

MAX_FILTERED_LINE_BYTES = 2000
HTTP_NOT_MODIFIED_STATUS = 304
_HEALTH_PATHS = {"/health", "/healthz", "/ping", "/ready", "/readyz", "/live", "/livez"}
_DOCKER_JSON_LINE_PATTERN = re.compile(r"^\S+\s+({.*})\s*$")


def _snapshot_dir_from_metadata(metadata: LogSnapshotMetadata) -> str:
    """Return the relative snapshot directory represented by metadata files."""

    if not metadata.files:
        return ""
    return Path(metadata.files[0].output_file).parent.as_posix()


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

    def create_filtered_view(
        self,
        metadata: LogSnapshotMetadata,
        *,
        source_contexts: dict[str, SourceNoiseContext],
        source_keys: list[str] | None,
        max_lines: int,
        requested_project_name: str | None,
        project_name: str,
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

        if source_keys:
            available_source_keys = {item.source_key for item in metadata.files}
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

        selected_files = [
            item for item in metadata.files if source_keys is None or item.source_key in source_keys
        ]
        searched_source_keys = [item.source_key for item in selected_files]
        cleaned_lines: list[SnapshotLineReferencePayload] = []
        source_summaries: dict[str, SourceFilteredSummary] = {}
        total_line_count = 0
        kept_line_count = 0
        excluded_line_count = 0

        for item in selected_files:
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
            output_path = resolve_snapshot_file_path(item)
            with output_path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    line = raw_line.rstrip("\n")
                    total_line_count += 1
                    summary.total_line_count += 1
                    decision = self._apply_noise_profile(context, line)
                    truncated_line, line_truncated = self._truncate_line(line)
                    if decision.keep:
                        kept_line_count += 1
                        summary.kept_line_count += 1
                        if len(cleaned_lines) < max_lines:
                            cleaned_lines.append(
                                SnapshotLineReferencePayload(
                                    source_key=item.source_key,
                                    output_file=item.output_file,
                                    line_number=line_number,
                                    line=truncated_line,
                                    line_truncated=line_truncated,
                                )
                            )
                    else:
                        excluded_line_count += 1
                        summary.excluded_line_count += 1
                        assert summary.exclusion_reasons is not None
                        summary.exclusion_reasons[decision.reason or "excluded_by_profile"] += 1

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

    def _apply_noise_profile(self, context: SourceNoiseContext, raw_line: str) -> FilterDecision:
        """Apply the manifest-selected noise profile to one raw line."""

        profile_name = context.default_noise_profile
        if not profile_name:
            return FilterDecision(keep=True)

        parsed = self._parse_json_line(raw_line)
        request_path = self._extract_request_path(parsed, raw_line)
        status_code = self._extract_status_code(parsed)
        level = self._extract_level(parsed)

        if profile_name == "web_noise":
            return self._filter_web_noise(context, request_path, status_code)
        if profile_name == "proxy_noise":
            return self._filter_proxy_noise(context, request_path, status_code)
        if profile_name == "backend_noise":
            return self._filter_backend_noise(request_path, status_code, level)
        if profile_name == "frontend_noise":
            return self._filter_frontend_noise(context, request_path, status_code, level)
        return FilterDecision(keep=True)

    @staticmethod
    def _filter_web_noise(
        context: SourceNoiseContext,
        request_path: str | None,
        status_code: int | None,
    ) -> FilterDecision:
        if request_path is None:
            return FilterDecision(keep=True)
        if request_path in _HEALTH_PATHS and (status_code is None or status_code < 500):
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
        if request_path in _HEALTH_PATHS and (status_code is None or status_code < 500):
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
        level: str | None,
    ) -> FilterDecision:
        if request_path in _HEALTH_PATHS and level in {"info", "debug", "trace"}:
            return FilterDecision(keep=False, reason="application_health_check_log")
        if request_path in _HEALTH_PATHS and status_code is not None and status_code < 500:
            return FilterDecision(keep=False, reason="successful_health_request_log")
        return FilterDecision(keep=True)

    @staticmethod
    def _filter_frontend_noise(
        context: SourceNoiseContext,
        request_path: str | None,
        status_code: int | None,
        level: str | None,
    ) -> FilterDecision:
        if request_path in _HEALTH_PATHS and level in {"info", "debug", "trace"}:
            return FilterDecision(keep=False, reason="frontend_health_check_log")
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
            for key in ("request_path", "path", "url_path", "uri"):
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
    def _extract_status_code(parsed: dict[str, Any] | None) -> int | None:
        if parsed is None:
            return None
        for key in ("status", "status_code", "statusCode"):
            value = parsed.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None

    @staticmethod
    def _extract_level(parsed: dict[str, Any] | None) -> str | None:
        if parsed is None:
            return None
        value = parsed.get("level")
        if isinstance(value, str):
            return value.strip().lower()
        return None

    @staticmethod
    def _truncate_line(value: str) -> tuple[str, bool]:
        encoded = value.encode("utf-8")
        if len(encoded) <= MAX_FILTERED_LINE_BYTES:
            return value, False
        return encoded[:MAX_FILTERED_LINE_BYTES].decode("utf-8", errors="ignore"), True
