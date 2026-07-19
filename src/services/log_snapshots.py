"""Snapshot lifecycle services for persisted log collection artifacts."""

from __future__ import annotations

import asyncio
import fcntl
import shutil
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from conf import settings
from core.types import LogWorkspace
from database.schemas import CollectLogsSourceOut
from database.services.collect_logs import CollectLogsService as CollectLogsDBService
from exceptions import MissingSessionIdError
from storage import LogFileStorage
from storage import storage as default_storage
from tools.models import (
    GrepLogSnapshotMatchPayload,
    LogSnapshotFilePayload,
    LogSnapshotMetadata,
    SnapshotWorkspace,
)
from tools.utils import cleanup_old_snapshot_dirs, parse_snapshot_retention
from utils.log_snapshots import (
    build_snapshot_not_found_retry_tips,
    classify_snapshot_tool_error,
    is_collection_diagnostics_source_key,
)

DEFAULT_GREP_MATCH_LIMIT = 100
MAX_GREP_MATCH_LINE_BYTES = 2000


@dataclass(slots=True)
class SnapshotContext:
    """Resolved snapshot metadata and filesystem paths for one snapshot request."""

    project_name: str
    caller_id: int
    snapshot_dir: Path
    metadata: LogSnapshotMetadata
    sources: list[CollectLogsSourceOut]


@dataclass(frozen=True, slots=True)
class SnapshotLookupError:
    """Lookup failure returned to tools instead of raising expected errors."""

    message: str
    error_code: str
    retry_tips: list[str]


@dataclass(frozen=True, slots=True)
class SnapshotReadError:
    """Expected source-file read failure returned to tools without exceptions."""

    message: str
    error_code: str
    retry_tips: list[str]


@dataclass(frozen=True, slots=True)
class SnapshotGrepError:
    """Expected grep failure returned to tools without exceptions."""

    message: str
    error_code: str
    retry_tips: list[str]


class SnapshotReadChunk(BaseModel):
    """Selected content and effective line window for one snapshot file read."""

    content: str
    start_line: int | None
    line_count: int | None


class LogSnapshotService:
    """Manage persisted log snapshot workspaces and on-disk lifecycle.

    Responsibility:

    - prepare workflow and session snapshot directories
    - archive or replace older persisted workspaces when policy requires it
    - load snapshot context for follow-up read/grep operations
    - run grep against already persisted snapshot files

    This service is the persistence boundary for collected logs. It owns
    snapshot directory layout and snapshot lifecycle rules, but it does not
    collect raw logs itself. Raw file/docker collection and request assembly
    belong to `LogCollectionService`.
    """

    def __init__(
        self,
        *,
        collect_logs_db_service: CollectLogsDBService | None = None,
        storage: LogFileStorage | None = None,
    ) -> None:
        self.collect_logs_db_service = collect_logs_db_service or CollectLogsDBService()
        self.storage = storage or default_storage

    def prepare_workspace(
        self,
        *,
        project_name: str,
        workspace: SnapshotWorkspace,
        session_id: str | None,
        snapshot_dir: str | Path | None = None,
    ) -> Path:
        """Prepare and return the final snapshot directory for one collection run.

        Workflow uses the DB-provided snapshot_dir path. Session returns the
        direct project folder under the provided `session_id`.
        """

        if workspace == LogWorkspace.WORKFLOW:
            if snapshot_dir is None:
                raise ValueError("snapshot_dir is required when workspace='workflow'.")
            return self._prepare_workflow_snapshot_dir(snapshot_dir)

        if not session_id or not session_id.strip():
            raise MissingSessionIdError(
                "session_id is required when workspace='session'. Reuse the same "
                "session_id for follow-up collection calls in the same agent session."
            )
        snapshot_output_path = self._prepare_session_snapshot_dir(
            project_name,
            session_id.strip(),
        )
        return snapshot_output_path

    @asynccontextmanager
    async def collection_transaction(self) -> AsyncIterator[None]:
        """Serialize every snapshot mutation transaction across processes."""

        lock_path = self.storage.location / ".snapshot-collection.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = lock_path.open("a+b")
        acquired = False
        try:
            while not acquired:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    await asyncio.sleep(0.01)
            yield
        finally:
            if acquired:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()

    async def load_snapshot(
        self,
        *,
        project_name: str,
        workspace: SnapshotWorkspace,
        session_id: str | None = None,
        archive_name: str | None = None,
    ) -> SnapshotContext | SnapshotLookupError:
        """Load one persisted workflow or session snapshot context.

        Callers pass the logical snapshot identity only. Session and workflow
        lookup methods own the DB query and pydantic contract creation.
        """

        if workspace == LogWorkspace.SESSION:
            return await self.load_session_snapshot(
                project_name=project_name,
                session_id=session_id,
            )
        return await self.load_workflow_snapshot(
            project_name=project_name,
            archive_name=archive_name,
        )

    async def load_session_snapshot(
        self,
        *,
        project_name: str,
        session_id: str | None,
    ) -> SnapshotContext | SnapshotLookupError:
        """Load one session snapshot from persisted collect_logs DB objects."""

        if not session_id or not session_id.strip():
            return self._build_snapshot_lookup_error(
                "session_id is required for session log snapshots.",
                workspace=LogWorkspace.SESSION,
            )
        collect_logs = await self.collect_logs_db_service.get_session_collect_logs_with_sources(
            project_name=project_name,
            session_id=session_id.strip(),
        )
        if collect_logs is None:
            return self._build_snapshot_lookup_error(
                "Requested session log snapshot was not found.",
                workspace=LogWorkspace.SESSION,
            )

        metadata = LogSnapshotMetadata(
            project_name=collect_logs.project_name,
            workspace=collect_logs.workspace,
            session_id=(
                str(collect_logs.session_id) if collect_logs.session_id is not None else None
            ),
            collected_at=collect_logs.collected_at.isoformat(),
            files=[
                self.source_to_file_payload(source)
                for source in collect_logs.sources
                if source.status == "collected" and source.file is not None
            ],
        )
        snapshot_dir = Path(collect_logs.snapshot_dir)
        if not snapshot_dir.is_absolute():
            snapshot_dir = self.storage.path(snapshot_dir)
        return SnapshotContext(
            project_name=collect_logs.project_name,
            caller_id=collect_logs.caller_id,
            snapshot_dir=snapshot_dir,
            metadata=metadata,
            sources=collect_logs.sources,
        )

    async def load_workflow_snapshot(
        self,
        *,
        project_name: str,
        archive_name: str | None,
    ) -> SnapshotContext | SnapshotLookupError:
        """Load workflow latest or archived snapshot from persisted DB objects."""

        if archive_name is None:
            collect_logs = await self.collect_logs_db_service.get_latest_with_sources(project_name)
        else:
            collect_logs = await self.collect_logs_db_service.get_archive_with_sources(
                project_name=project_name,
                archive_name=archive_name,
            )

        if collect_logs is None:
            return self._build_snapshot_lookup_error(
                "Requested workflow log snapshot was not found.",
                workspace=LogWorkspace.WORKFLOW,
            )

        metadata = LogSnapshotMetadata(
            project_name=collect_logs.project_name,
            workspace=collect_logs.workspace,
            session_id=(
                str(collect_logs.session_id) if collect_logs.session_id is not None else None
            ),
            collected_at=collect_logs.collected_at.isoformat(),
            files=[
                self.source_to_file_payload(source)
                for source in collect_logs.sources
                if source.status == "collected" and source.file is not None
            ],
        )
        snapshot_dir = Path(collect_logs.snapshot_dir)
        if not snapshot_dir.is_absolute():
            snapshot_dir = self.storage.path(snapshot_dir)
        return SnapshotContext(
            project_name=collect_logs.project_name,
            caller_id=collect_logs.caller_id,
            snapshot_dir=snapshot_dir,
            metadata=metadata,
            sources=collect_logs.sources,
        )

    @staticmethod
    def source_to_file_payload(source: CollectLogsSourceOut) -> LogSnapshotFilePayload:
        """Return the snapshot file contract for one collected source DB contract."""

        assert source.file is not None
        try:
            byte_count = source.file.size
        except (FileNotFoundError, ValueError):
            byte_count = 0
        return LogSnapshotFilePayload(
            source_key=source.source_key,
            source_type=source.source_type,
            description=source.description,
            target=source.target,
            stream=source.stream,
            parser_type=source.parser_type,
            normalization_profile=source.normalization_profile,
            default_noise_profile=source.default_noise_profile,
            file_name=Path(source.file.name).name,
            output_file=source.file.name,
            byte_count=byte_count,
            line_count=source.line_count,
        )

    @staticmethod
    def find_snapshot_source(
        sources: list[CollectLogsSourceOut],
        *,
        source_key: str,
    ) -> CollectLogsSourceOut | SnapshotReadError:
        """Return one collected DB source row for a source key."""

        source = next((item for item in sources if item.source_key == source_key), None)
        if source is None or source.status != "collected" or source.file is None:
            return SnapshotReadError(
                message="Requested log snapshot source_key was not found.",
                error_code="snapshot_source_key_not_found",
                retry_tips=[
                    (
                        "Retry with a source_key returned by collect_logs or "
                        "call list_log_snapshot_files to list files/sources "
                        "available inside one already-collected snapshot."
                    ),
                ],
            )
        return source

    @staticmethod
    def read_snapshot_source(source: CollectLogsSourceOut) -> str | SnapshotReadError:
        """Read one collected source through its DB file reference."""

        if source.file is None:
            return SnapshotReadError(
                message="Requested log snapshot source_key was not found.",
                error_code="snapshot_source_key_not_found",
                retry_tips=["Retry with a source_key returned by list_log_snapshot_files."],
            )
        try:
            with open(source.file.path, encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except ValueError:
            return SnapshotReadError(
                message="Requested persisted source file reference is invalid.",
                error_code="invalid_snapshot_file_metadata",
                retry_tips=[
                    "Run collect_logs again to recreate the persisted source file reference.",
                ],
            )
        except OSError:
            return SnapshotReadError(
                message="Requested log snapshot file was not found on disk.",
                error_code="snapshot_file_not_found",
                retry_tips=[
                    "Run collect_logs again to recreate the missing persisted file.",
                ],
            )

    @staticmethod
    def _build_snapshot_lookup_error(
        message: str,
        *,
        workspace: SnapshotWorkspace,
    ) -> SnapshotLookupError:
        """Return the public lookup error for one failed snapshot load."""

        error_code = classify_snapshot_tool_error(
            message,
            default_error_code="log_snapshot_not_found",
        )
        if workspace == LogWorkspace.SESSION:
            retry_tips = (
                [
                    "Run collect_logs with the same session_id and project_name first.",
                    "Retry with the exact session_id used for this investigation.",
                ]
                if error_code == "snapshot_not_found"
                else [
                    (
                        "Retry with a valid session_id and project_name "
                        "from the authorized investigation."
                    ),
                ]
            )
        else:
            retry_tips = (
                build_snapshot_not_found_retry_tips()
                if error_code == "snapshot_not_found"
                else [
                    "Retry without archive_name for the newest workflow artifact, "
                    "or with a valid archive_name."
                ]
            )
        return SnapshotLookupError(
            message=message,
            error_code=error_code,
            retry_tips=retry_tips,
        )

    @staticmethod
    def grep_snapshot(
        context: SnapshotContext,
        *,
        grep: str,
        source_keys: list[str] | None,
        match_offset: int,
        max_matches: int,
    ) -> tuple[list[GrepLogSnapshotMatchPayload], int] | SnapshotGrepError:
        """Search persisted snapshot files with a controlled grep invocation.

        Source keys are validated against DB-backed source contracts before any
        file is opened. File paths are read from the source file reference. The
        grep process is bounded by
        `match_offset` and `max_matches` for the returned page, while still
        reporting the total match count.

        The returned tuple contains:

        - the requested page of line matches
        - the total number of grep matches across the selected snapshot files

        Expected source-key, path, and grep-process failures are returned as
        `SnapshotGrepError` so MCP tools can return a structured error response
        without catching expected service exceptions.
        """

        available_sources: list[CollectLogsSourceOut] = [
            source
            for source in context.sources
            if source.status == "collected" and source.file is not None
        ]
        if source_keys:
            available_source_keys: set[str] = {item.source_key for item in available_sources}
            unknown_source_keys: list[str] = sorted(set(source_keys) - available_source_keys)
            if unknown_source_keys:
                return SnapshotGrepError(
                    message=(
                        "Requested log snapshot source_keys were not found: "
                        + ", ".join(unknown_source_keys)
                    ),
                    error_code="snapshot_source_key_not_found",
                    retry_tips=[
                        "Retry with source_keys returned by list_log_snapshot_files.",
                    ],
                )

        selected_sources: list[CollectLogsSourceOut] = (
            [item for item in available_sources if item.source_key in set(source_keys or [])]
            if source_keys
            else [
                item
                for item in available_sources
                if not is_collection_diagnostics_source_key(item.source_key)
            ]
        )
        if not selected_sources:
            return [], 0

        grepped_sources: dict[str, CollectLogsSourceOut] = {}
        for source in selected_sources:
            assert source.file is not None
            try:
                source_file_path: str = source.file.path
            except ValueError as error:
                return SnapshotGrepError(
                    message=str(error),
                    error_code="invalid_snapshot_file_metadata",
                    retry_tips=[
                        "Run collect_logs again to recreate the persisted source file reference.",
                    ],
                )
            except FileNotFoundError as error:
                return SnapshotGrepError(
                    message=str(error),
                    error_code="snapshot_file_not_found",
                    retry_tips=[
                        "Run collect_logs again to recreate the missing persisted file.",
                    ],
                )
            if not Path(source_file_path).exists():
                return SnapshotGrepError(
                    message="Requested log snapshot file was not found on disk.",
                    error_code="snapshot_file_not_found",
                    retry_tips=[
                        "Run collect_logs again to recreate the missing persisted file.",
                    ],
                )
            grepped_sources[source_file_path] = source

        grep_command: list[str] = [
            "grep",
            "-E",
            "-H",
            "-n",
            "--",
            grep,
            *grepped_sources.keys(),
        ]
        completed: subprocess.CompletedProcess[str] = subprocess.run(
            grep_command,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode not in {0, 1}:
            error_output: str = (
                completed.stderr.strip() or completed.stdout.strip() or "grep failed."
            )
            return SnapshotGrepError(
                message=error_output,
                error_code="snapshot_grep_failed",
                retry_tips=[
                    "Retry with a simpler grep pattern or collect_logs again before searching.",
                ],
            )
        if completed.returncode == 1:
            return [], 0

        all_match_lines: list[str] = completed.stdout.splitlines()
        total_match_count: int = len(all_match_lines)
        selected_match_lines: list[str] = all_match_lines[match_offset : match_offset + max_matches]

        matches: list[GrepLogSnapshotMatchPayload] = []
        for raw_line in selected_match_lines:
            source_file_path, line_number, line = raw_line.split(":", 2)
            matched_source: CollectLogsSourceOut = grepped_sources[source_file_path]
            assert matched_source.file is not None
            encoded_line: bytes = line.encode("utf-8")
            line_truncated: bool = len(encoded_line) > MAX_GREP_MATCH_LINE_BYTES
            if line_truncated:
                line = encoded_line[:MAX_GREP_MATCH_LINE_BYTES].decode(
                    "utf-8",
                    errors="ignore",
                )
            matches.append(
                GrepLogSnapshotMatchPayload(
                    source_key=matched_source.source_key,
                    output_file=matched_source.file.name,
                    line_number=int(line_number),
                    line=line,
                    line_truncated=line_truncated,
                )
            )
        return matches, total_match_count

    @staticmethod
    def select_snapshot_read_chunk(
        full_content: str,
        *,
        start_line: int | None,
        line_count: int | None,
    ) -> SnapshotReadChunk | SnapshotReadError:
        """Select one agent-facing line range from a persisted snapshot file body.

        `start_line` is one-based because it matches line numbers returned by
        grep and analysis tools. When both `start_line` and `line_count` are
        omitted, the full content is returned with effective range metadata.
        Empty files return a chunk with `start_line=None` and `line_count=0`.

        Invalid ranges are returned as `SnapshotReadError` so the tool layer
        can return the normal structured error payload without exceptions.
        """

        lines = full_content.splitlines(keepends=True)
        if start_line is None and line_count is None:
            return SnapshotReadChunk(
                content=full_content,
                start_line=1 if lines else None,
                line_count=len(lines) if lines else 0,
            )

        effective_start_line = 1 if start_line is None else start_line
        if effective_start_line > len(lines) and lines:
            return SnapshotReadError(
                message="Requested snapshot read range starts beyond the end of the file.",
                error_code="invalid_snapshot_read_range",
                retry_tips=[
                    "Retry with start_line inside the line count returned by the snapshot file.",
                ],
            )

        start_index = effective_start_line - 1
        end_index = None if line_count is None else start_index + line_count
        selected_lines = lines[start_index:end_index]
        return SnapshotReadChunk(
            content="".join(selected_lines),
            start_line=effective_start_line,
            line_count=len(selected_lines),
        )

    def _prepare_workflow_snapshot_dir(
        self,
        snapshot_dir: str | Path,
    ) -> Path:
        """Prepare the workflow latest directory for direct writes.

        This method:

        - resolves the project's workflow `latest` and `archive` directories
        - recreates an empty `latest` directory for direct writes
        """

        latest_output_dir: Path = Path(snapshot_dir)
        if not latest_output_dir.is_absolute():
            latest_output_dir = self.storage.path(latest_output_dir)
        self.storage.ensure_dir(latest_output_dir.parent / "archive")
        if latest_output_dir.exists():
            shutil.rmtree(latest_output_dir)
        latest_output_dir.mkdir(parents=True, exist_ok=True)
        return latest_output_dir

    def _prepare_session_snapshot_dir(
        self,
        project_name: str,
        session_id: str,
    ) -> Path:
        """Prepare one empty session project directory for direct writes.

        Session snapshots are replace-in-place per `(session_id, project_name)`.
        The session root is retained according to `LOG_SNAPSHOT_RETENTION`, and
        the target project directory is recreated empty before collection.
        """

        sessions_root_dir = self.storage.session_path
        new_session_dir = self.storage.ensure_dir(sessions_root_dir / session_id)
        cleanup_old_snapshot_dirs(
            sessions_root_dir,
            retention=parse_snapshot_retention(settings.LOG_SNAPSHOT_RETENTION),
        )
        project_session_dir = new_session_dir / project_name
        if project_session_dir.exists():
            shutil.rmtree(project_session_dir)
        project_session_dir.mkdir(parents=True, exist_ok=True)
        return project_session_dir
