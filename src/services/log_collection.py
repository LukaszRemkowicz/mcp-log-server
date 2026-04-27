"""Collection orchestration service for persisted project log snapshots."""

from __future__ import annotations

from collections.abc import Callable

from fastmcp.server.auth import AccessToken

from manifests.models import SourceDefinition, SourceManifest
from settings import Settings
from tools.models import CollectedSourcePayload, CollectLogsPayload, SnapshotWorkspace
from tools.utils import load_authorized_project_manifest
from utils.log_preview import truncate_collected_sources_for_response

from .log_snapshots import LogSnapshotService

MAX_TAIL_LINES = 1000
MAX_INLINE_LOG_BYTES = 200_000


class LogCollectionService:
    """Orchestrate one complete `collect_logs` request end-to-end.

    Responsibility:

    - authorize and normalize the collection request
    - resolve manifest-backed source definitions from requested source keys
    - build deterministic warnings and retry tips
    - call the low-level source collector for each resolved source
    - delegate snapshot directory preparation and persistence to
      `LogSnapshotService`
    - apply response preview truncation before returning the final payload
    - assemble the agent-facing `CollectLogsPayload`

    This service is intentionally the orchestration layer. It does not own
    the low-level file/docker collection adapters and it does not own the
    persisted snapshot directory rules. Those concerns are delegated to
    `LogSourceCollectionService` and `LogSnapshotService` respectively.
    """

    def __init__(
        self,
        settings: Settings,
        access_token: AccessToken,
        *,
        snapshot_service: LogSnapshotService,
        source_collector: Callable[..., CollectedSourcePayload],
        tail_line_limiter: Callable[[int], int],
        response_truncator: Callable[
            [list[CollectedSourcePayload]],
            bool,
        ] = truncate_collected_sources_for_response,
    ) -> None:
        self.settings = settings
        self.access_token = access_token
        self.snapshot_service = snapshot_service
        self.source_collector = source_collector
        self.tail_line_limiter = tail_line_limiter
        self.response_truncator = response_truncator

    def build_payload(
        self,
        *,
        requested_project_name: str | None,
        requested_source_keys: list[str] | None,
        workspace: SnapshotWorkspace,
        session_id: str | None,
        tail_lines: int | None,
        timestamps: bool,
        since: str | None,
        until: str | None,
    ) -> CollectLogsPayload:
        """Build the agent-facing collection payload and persist the snapshot."""

        (
            _manifest,
            authorized_project_name,
            effective_project_name,
            effective_since,
            bounded_tail_lines,
            tail_lines_limited,
            resolved_sources,
            unknown_source_keys,
            resolved_source_keys,
        ) = self._normalize_request(
            requested_project_name=requested_project_name,
            requested_source_keys=requested_source_keys,
            tail_lines=tail_lines,
            since=since,
        )
        warnings, retry_tips = self._build_feedback(
            tail_lines=tail_lines,
            bounded_tail_lines=bounded_tail_lines,
            tail_lines_limited=tail_lines_limited,
            unknown_source_keys=unknown_source_keys,
        )
        collected_sources = self._collect_sources(
            resolved_sources,
            bounded_tail_lines=bounded_tail_lines,
            timestamps=timestamps,
            since=effective_since,
            until=until,
        )
        self._append_large_source_feedback(collected_sources, warnings, retry_tips)
        (
            project_output_path,
            snapshot_output_path,
            snapshot_id,
            latest_output_dir,
            archive_dir,
        ) = self.snapshot_service.prepare_workspace(
            effective_project_name=effective_project_name,
            workspace=workspace,
            session_id=session_id,
        )
        collected_at, collected_at_file, metadata_file = self.snapshot_service.persist_outputs(
            snapshot_output_path,
            project_name=effective_project_name,
            workspace=workspace,
            snapshot_id=snapshot_id,
            collected_sources=collected_sources,
        )
        self.response_truncator(collected_sources)
        return self._build_response(
            requested_project_name=requested_project_name,
            requested_source_keys=requested_source_keys,
            authorized_project_name=authorized_project_name,
            effective_project_name=effective_project_name,
            workspace=workspace,
            snapshot_id=snapshot_id,
            session_id=snapshot_id,
            snapshot_dir=str(snapshot_output_path),
            metadata_file=metadata_file,
            requested_tail_lines=tail_lines,
            effective_tail_lines=bounded_tail_lines,
            requested_timestamps=timestamps,
            requested_since=effective_since,
            requested_until=until,
            tail_lines_limited=tail_lines_limited,
            warnings=warnings,
            retry_tips=retry_tips,
            unknown_source_keys=unknown_source_keys,
            resolved_source_keys=resolved_source_keys,
            project_output_dir=str(project_output_path),
            latest_output_dir=latest_output_dir,
            archive_dir=archive_dir,
            collected_at=collected_at,
            collected_at_file=collected_at_file,
            collected_sources=collected_sources,
        )

    def _normalize_request(
        self,
        *,
        requested_project_name: str | None,
        requested_source_keys: list[str] | None,
        tail_lines: int | None,
        since: str | None,
    ) -> tuple[
        SourceManifest,
        str,
        str,
        str,
        int | None,
        bool,
        list[SourceDefinition],
        list[str],
        list[str],
    ]:
        """Resolve project/source context and normalize collection arguments."""

        (
            manifest,
            authorized_project_name,
            effective_project_name,
        ) = load_authorized_project_manifest(
            self.settings,
            self.access_token,
            requested_project_name,
        )
        effective_since = since or self.settings.DEFAULT_LOG_WINDOW
        bounded_tail_lines = None if tail_lines is None else self.tail_line_limiter(tail_lines)
        resolved_sources, unknown_source_keys, resolved_source_keys = self.resolve_manifest_sources(
            manifest,
            requested_source_keys,
        )
        tail_lines_limited = bounded_tail_lines != tail_lines
        return (
            manifest,
            authorized_project_name,
            effective_project_name,
            effective_since,
            bounded_tail_lines,
            tail_lines_limited,
            resolved_sources,
            unknown_source_keys,
            resolved_source_keys,
        )

    @staticmethod
    def _build_feedback(
        *,
        tail_lines: int | None,
        bounded_tail_lines: int | None,
        tail_lines_limited: bool,
        unknown_source_keys: list[str],
    ) -> tuple[list[str], list[str]]:
        """Build deterministic warnings and retry tips for one collection request."""

        warnings: list[str] = []
        retry_tips: list[str] = []

        if tail_lines_limited:
            warnings.append(
                f"Requested tail_lines={tail_lines} exceeded the server limit of {MAX_TAIL_LINES}. "
                f"Using {bounded_tail_lines} instead."
            )
            retry_tips.append(
                f"Retry with tail_lines <= {MAX_TAIL_LINES} to avoid server-side limiting."
            )
        if tail_lines is None:
            warnings.append(
                "No tail_lines value was provided. Full source output will be "
                "requested where supported."
            )
            retry_tips.append(
                "Retry with tail_lines to keep docker and file collection bounded "
                "if a source is slow or large."
            )
        if unknown_source_keys:
            warnings.append(
                "Some requested source_keys were not found in the configured manifest: "
                + ", ".join(unknown_source_keys)
                + "."
            )
            retry_tips.append(
                "Retry with only source_keys returned by the manifest-backed project configuration."
            )

        return warnings, retry_tips

    def _collect_sources(
        self,
        resolved_sources: list[SourceDefinition],
        *,
        bounded_tail_lines: int | None,
        timestamps: bool,
        since: str | None,
        until: str | None,
    ) -> list[CollectedSourcePayload]:
        """Collect all requested manifest sources with normalized request options."""

        return [
            self.source_collector(
                source,
                bounded_tail_lines,
                timestamps=timestamps,
                since=since,
                until=until,
            )
            for source in resolved_sources
            if source.source_type == "docker" or source.source_type == "file"
        ]

    @staticmethod
    def _append_large_source_feedback(
        collected_sources: list[CollectedSourcePayload],
        warnings: list[str],
        retry_tips: list[str],
    ) -> None:
        """Append guidance when one or more collected sources exceed inline preview size."""

        auto_persist_large_logs = any(
            source.status == "collected" and source.byte_count > MAX_INLINE_LOG_BYTES
            for source in collected_sources
        )
        if not auto_persist_large_logs:
            return

        warnings.append(
            "One or more collected sources were too large for an in-response body. "
            "The full filtered snapshot was saved to the project logs directory "
            "and only a preview was returned."
        )
        retry_tips.append("Use source output_file paths when you need the full saved log snapshot.")

    @staticmethod
    def _build_response(
        *,
        requested_project_name: str | None,
        requested_source_keys: list[str] | None,
        authorized_project_name: str,
        effective_project_name: str,
        workspace: SnapshotWorkspace,
        snapshot_id: str,
        session_id: str | None,
        snapshot_dir: str,
        metadata_file: str,
        requested_tail_lines: int | None,
        effective_tail_lines: int | None,
        requested_timestamps: bool,
        requested_since: str | None,
        requested_until: str | None,
        tail_lines_limited: bool,
        warnings: list[str],
        retry_tips: list[str],
        unknown_source_keys: list[str],
        resolved_source_keys: list[str],
        project_output_dir: str | None,
        latest_output_dir: str | None,
        archive_dir: str | None,
        collected_at: str,
        collected_at_file: str | None,
        collected_sources: list[CollectedSourcePayload],
    ) -> CollectLogsPayload:
        """Assemble the final agent-facing payload for one collection snapshot."""

        logs_by_source = {
            source.source_key: source.content for source in collected_sources if source.content
        }
        return CollectLogsPayload(
            action="collect_logs",
            requested_project_name=requested_project_name,
            authorized_project_name=authorized_project_name,
            effective_project_name=effective_project_name,
            workspace=workspace,
            session_id=None if workspace == "workflow" else session_id,
            snapshot_id=snapshot_id,
            snapshot_dir=snapshot_dir,
            metadata_file=metadata_file,
            persisted=True,
            requested_source_keys=requested_source_keys or [],
            requested_tail_lines=requested_tail_lines,
            effective_tail_lines=effective_tail_lines,
            requested_timestamps=requested_timestamps,
            requested_since=requested_since,
            requested_until=requested_until,
            tail_lines_limited=tail_lines_limited,
            warnings=warnings,
            retry_tips=retry_tips,
            unknown_requested_source_keys=unknown_source_keys,
            resolved_source_keys=resolved_source_keys,
            logs_by_source=logs_by_source,
            project_output_dir=project_output_dir,
            latest_output_dir=latest_output_dir,
            archive_dir=archive_dir,
            collected_at=collected_at,
            collected_at_file=collected_at_file,
            sources=collected_sources,
        )

    @staticmethod
    def resolve_manifest_sources(
        manifest: SourceManifest,
        requested_source_keys: list[str] | None,
    ) -> tuple[list[SourceDefinition], list[str], list[str]]:
        """Resolve requested manifest source keys into concrete source definitions."""

        if requested_source_keys is None:
            return list(manifest.sources), [], [source.source_key for source in manifest.sources]

        requested_lookup = set(requested_source_keys)
        resolved_sources = [
            source for source in manifest.sources if source.source_key in requested_lookup
        ]
        resolved_source_keys = [source.source_key for source in resolved_sources]
        unknown_source_keys = [
            key for key in requested_source_keys if key not in resolved_source_keys
        ]
        return resolved_sources, unknown_source_keys, resolved_source_keys
