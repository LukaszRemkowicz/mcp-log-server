"""Snapshot lifecycle services for persisted log collection artifacts."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from conf import settings
from tools.models import (
    GrepLogSnapshotMatchPayload,
    LogSnapshotFilePayload,
    LogSnapshotMetadata,
    SnapshotWorkspace,
    WorkflowArtifactMetadata,
    WorkflowProjectInventory,
)
from tools.utils import (
    SNAPSHOT_METADATA_FILE_NAME,
    WORKFLOW_INVENTORY_FILE_NAME,
    cleanup_old_snapshot_dirs,
    parse_snapshot_retention,
)
from utils.log_snapshots import (
    build_snapshot_not_found_retry_tips,
    classify_snapshot_tool_error,
    read_snapshot_metadata,
    read_workflow_inventory,
)

DEFAULT_GREP_MATCH_LIMIT = 100
MAX_GREP_MATCH_LINE_BYTES = 2000


@dataclass(slots=True)
class SnapshotContext:
    """Resolved snapshot metadata and filesystem paths for one snapshot request."""

    project_name: str
    snapshot_dir: Path
    metadata_file: Path
    metadata: LogSnapshotMetadata


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
    - write collected outputs plus snapshot metadata to disk
    - load authorized snapshot context for follow-up read/grep operations
    - run grep against already persisted snapshot files

    This service is the persistence boundary for collected logs. It owns
    snapshot directory layout and snapshot lifecycle rules, but it does not
    collect raw logs itself. Raw file/docker collection and request assembly
    belong to `LogCollectionService`.
    """

    def prepare_workspace(
        self,
        *,
        project_name: str,
        workspace: SnapshotWorkspace,
        session_id: str | None,
    ) -> Path:
        """Prepare and return the final snapshot directory for one collection run.

        Workflow returns the project's `latest` directory after archiving any
        previous `latest` artifact. Session returns the direct project folder
        under the provided `session_id`.
        """

        if workspace == "workflow":
            latest_output_path = self._prepare_workflow_snapshot_dir(project_name)
            return latest_output_path

        if not session_id or not session_id.strip():
            raise ValueError(
                "session_id is required when workspace='session'. Reuse the same "
                "session_id for follow-up collection calls in the same agent session."
            )
        snapshot_output_path = self._prepare_session_snapshot_dir(
            project_name,
            session_id.strip(),
        )
        return snapshot_output_path

    def write_metadata_files(
        self,
        snapshot_dir: Path,
        *,
        project_name: str,
        workspace: SnapshotWorkspace,
        session_id: str | None,
        collected_files: list[LogSnapshotFilePayload],
    ) -> SnapshotContext:
        """Write snapshot metadata for one completed collection artifact.

        Workflow collections update `workflow_inventory.json` and return a
        context backed by that inventory entry. Session collections write a
        local `snapshot_metadata.json` beside the collected files. In both
        cases, persisted file paths are converted to paths relative to
        `settings.LOGS_DIR` before being stored.
        """

        collected_at = datetime.now(UTC).isoformat()
        metadata_files = self._files_with_relative_output_paths(collected_files)

        if workspace == "workflow":
            metadata = LogSnapshotMetadata(
                project_name=project_name,
                workspace=workspace,
                collected_at=collected_at,
                files=metadata_files,
            )
            self.write_workflow_latest(
                project_name=project_name,
                latest_artifact=self._build_workflow_artifact_metadata(
                    snapshot_dir=snapshot_dir,
                    archive_name=None,
                    collected_at=collected_at,
                    files=metadata_files,
                ),
            )
            return SnapshotContext(
                project_name=project_name,
                snapshot_dir=snapshot_dir,
                metadata_file=self._workflow_inventory_file(project_name),
                metadata=metadata,
            )

        metadata_file = snapshot_dir / SNAPSHOT_METADATA_FILE_NAME
        metadata = LogSnapshotMetadata(
            project_name=project_name,
            workspace=workspace,
            session_id=session_id,
            collected_at=collected_at,
            files=metadata_files,
        )
        metadata_file.write_text(
            json.dumps(metadata.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        return SnapshotContext(
            project_name=project_name,
            snapshot_dir=snapshot_dir,
            metadata_file=metadata_file,
            metadata=metadata,
        )

    def load_snapshot(
        self,
        *,
        project_name: str,
        workspace: SnapshotWorkspace,
        session_id: str | None = None,
        archive_name: str | None = None,
    ) -> SnapshotContext | SnapshotLookupError:
        """Load one persisted workflow or session snapshot context.

        Callers pass the logical snapshot identity only. This service owns the
        on-disk lookup details: workflow snapshots are selected from the
        project inventory, while session snapshots are loaded from the
        session/project metadata file.
        """

        if workspace == "session":
            return self.load_session_snapshot(
                project_name=project_name,
                session_id=session_id,
            )
        return self.load_workflow_snapshot(
            project_name=project_name,
            archive_name=archive_name,
        )

    def load_session_snapshot(
        self,
        *,
        project_name: str,
        session_id: str | None,
    ) -> SnapshotContext | SnapshotLookupError:
        """Load one session snapshot from `sessions/<session_id>/<project_name>`.

        Missing or invalid metadata is returned as `SnapshotLookupError` so MCP
        tools can emit a structured retryable response instead of raising.
        """

        if not session_id or not session_id.strip():
            return self._build_snapshot_lookup_error(
                "session_id is required for session log snapshots.",
                workspace="session",
            )
        snapshot_dir = settings.session_path / session_id.strip() / project_name
        if not snapshot_dir.exists():
            return self._build_snapshot_lookup_error(
                "Requested session log snapshot was not found.",
                workspace="session",
            )
        try:
            metadata = read_snapshot_metadata(snapshot_dir)
        except ValueError as error:
            return self._build_snapshot_lookup_error(str(error), workspace="session")
        return SnapshotContext(
            project_name=project_name,
            snapshot_dir=snapshot_dir,
            metadata_file=snapshot_dir / SNAPSHOT_METADATA_FILE_NAME,
            metadata=metadata,
        )

    def load_workflow_snapshot(
        self,
        *,
        project_name: str,
        archive_name: str | None,
    ) -> SnapshotContext | SnapshotLookupError:
        """Load workflow latest or archived metadata from project inventory.

        `archive_name=None` selects the current latest artifact. Any archive
        name selects the matching archive entry. Workflow metadata is assembled
        from `workflow_inventory.json`; there is no separate per-snapshot
        metadata file for workflow artifacts.
        """

        try:
            workflow_entry = self._get_workflow_inventory_entry(project_name, archive_name)
            snapshot_dir = self._logs_absolute_path(workflow_entry.snapshot_dir)
        except ValueError as error:
            return self._build_snapshot_lookup_error(str(error), workspace="workflow")
        metadata = LogSnapshotMetadata(
            project_name=project_name,
            workspace="workflow",
            collected_at=workflow_entry.collected_at,
            files=workflow_entry.files,
        )
        return SnapshotContext(
            project_name=project_name,
            snapshot_dir=snapshot_dir,
            metadata_file=self._workflow_inventory_file(project_name),
            metadata=metadata,
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
        if workspace == "session":
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

    def grep_snapshot(
        self,
        metadata: LogSnapshotMetadata,
        *,
        grep: str,
        source_keys: list[str] | None,
        match_offset: int,
        match_limit: int,
    ) -> tuple[list[GrepLogSnapshotMatchPayload], int] | SnapshotGrepError:
        """Search persisted snapshot files with a controlled grep invocation.

        Source keys are validated against snapshot metadata before any file is
        opened. File paths are resolved through the same LOGS_DIR safety gate
        used by read/analysis tools. The grep process is bounded by
        `match_offset` and `match_limit` for the returned page, while still
        reporting the total match count.

        The returned tuple contains:

        - the requested page of line matches
        - the total number of grep matches across the selected snapshot files

        Expected source-key, path, and grep-process failures are returned as
        `SnapshotGrepError` so MCP tools can return a structured error response
        without catching expected service exceptions.
        """

        if source_keys:
            available_source_keys = {item.source_key for item in metadata.files}
            unknown_source_keys = sorted(set(source_keys) - available_source_keys)
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

        selected_files = (
            [item for item in metadata.files if item.source_key in set(source_keys or [])]
            if source_keys
            else list(metadata.files)
        )
        if not selected_files:
            return [], 0

        file_path_to_source_key: dict[str, str] = {}
        for item in selected_files:
            try:
                file_path = self._resolve_snapshot_file_path_or_raise(item)
            except ValueError as error:
                return SnapshotGrepError(
                    message=str(error),
                    error_code="invalid_snapshot_file_metadata",
                    retry_tips=[
                        "Run collect_logs again to recreate snapshot metadata for this project.",
                    ],
                )
            file_path_to_source_key[str(file_path)] = item.source_key

        grep_command = ["grep", "-H", "-n", "--", grep, *file_path_to_source_key.keys()]
        completed = subprocess.run(
            grep_command,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode not in {0, 1}:
            error_output = completed.stderr.strip() or completed.stdout.strip() or "grep failed."
            return SnapshotGrepError(
                message=error_output,
                error_code="snapshot_grep_failed",
                retry_tips=[
                    "Retry with a simpler grep pattern or collect_logs again before searching.",
                ],
            )
        if completed.returncode == 1:
            return [], 0

        all_match_lines = completed.stdout.splitlines()
        total_match_count = len(all_match_lines)
        selected_match_lines = all_match_lines[match_offset : match_offset + match_limit]

        matches: list[GrepLogSnapshotMatchPayload] = []
        for raw_line in selected_match_lines:
            matched_file_path, line_number, line = raw_line.split(":", 2)
            encoded_line = line.encode("utf-8")
            line_truncated = len(encoded_line) > MAX_GREP_MATCH_LINE_BYTES
            if line_truncated:
                line = encoded_line[:MAX_GREP_MATCH_LINE_BYTES].decode(
                    "utf-8",
                    errors="ignore",
                )
            matches.append(
                GrepLogSnapshotMatchPayload(
                    source_key=file_path_to_source_key[matched_file_path],
                    output_file=matched_file_path,
                    line_number=int(line_number),
                    line=line,
                    line_truncated=line_truncated,
                )
            )
        return matches, total_match_count

    @staticmethod
    def find_snapshot_file(
        metadata: LogSnapshotMetadata,
        *,
        source_key: str,
    ) -> LogSnapshotFilePayload | SnapshotReadError:
        """Return persisted source-file metadata for one source key.

        Snapshot follow-up tools use this after loading snapshot metadata and
        before resolving the file path on disk.

        Missing sources are returned as `SnapshotReadError` so MCP tools can
        build the normal error response without broad exception handling.
        """

        file_payload = next(
            (item for item in metadata.files if item.source_key == source_key),
            None,
        )
        if file_payload is None:
            return SnapshotReadError(
                message="Requested log snapshot source_key was not found.",
                error_code="snapshot_source_key_not_found",
                retry_tips=[
                    (
                        "Retry with a valid archive_name and source_key returned by "
                        "collect_logs or list_log_snapshot_files."
                    ),
                ],
            )
        return file_payload

    @staticmethod
    def resolve_snapshot_file_path(
        file_payload: LogSnapshotFilePayload,
    ) -> Path | SnapshotReadError:
        """Resolve one metadata output file into a safe absolute path.

        Snapshot metadata stores source file paths relative to `settings.LOGS_DIR`.
        This method is the safety gate before opening any persisted snapshot
        file: it rejects empty paths, absolute paths, parent-directory traversal,
        paths escaping the configured logs root after resolution, and missing
        files.

        Expected path failures are returned as `SnapshotReadError` so callers
        can map them to structured MCP errors without broad exception handling.
        """

        output_file = file_payload.output_file.strip()
        normalized_output_file = Path(output_file)
        if (
            not output_file
            or normalized_output_file.is_absolute()
            or ".." in normalized_output_file.parts
        ):
            return SnapshotReadError(
                message="Requested log snapshot file metadata is invalid.",
                error_code="invalid_snapshot_file_metadata",
                retry_tips=[
                    "Run collect_logs again to recreate snapshot metadata for this project.",
                ],
            )

        resolved_logs_dir = settings.LOGS_DIR.resolve()
        resolved_file_path = (resolved_logs_dir / normalized_output_file).resolve()
        if resolved_logs_dir not in resolved_file_path.parents:
            return SnapshotReadError(
                message="Requested log snapshot file escapes the configured logs directory.",
                error_code="invalid_snapshot_file_metadata",
                retry_tips=[
                    "Run collect_logs again to recreate snapshot metadata for this project.",
                ],
            )
        if not resolved_file_path.exists():
            return SnapshotReadError(
                message="Requested log snapshot file was not found on disk.",
                error_code="snapshot_file_not_found",
                retry_tips=[
                    "Run collect_logs again to recreate the missing persisted file.",
                ],
            )
        return resolved_file_path

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

    def _resolve_snapshot_file_path_or_raise(
        self,
        file_payload: LogSnapshotFilePayload,
    ) -> Path:
        """Resolve a snapshot file path for internal services that still raise."""

        result = LogSnapshotService.resolve_snapshot_file_path(file_payload)
        if isinstance(result, SnapshotReadError):
            raise ValueError(result.message)
        return result

    def _prepare_workflow_snapshot_dir(
        self,
        project_name: str,
    ) -> Path:
        """Prepare the workflow latest directory by archiving the previous artifact first.

        This method:

        - resolves the project's workflow `latest` and `archive` directories
        - applies archive retention cleanup
        - archives the previous `latest` artifact when present
        - recreates an empty `latest` directory for direct writes
        """

        latest_output_dir, archive_dir = self.create_workflow_dirs(project_name)
        cleanup_old_snapshot_dirs(
            archive_dir,
            retention=parse_snapshot_retention(settings.WORKFLOW_ARCHIVE_RETENTION),
        )
        self.archive_workflow_latest(
            project_name=project_name,
            latest_output_dir=latest_output_dir,
            archive_dir=archive_dir,
        )
        if latest_output_dir.exists():
            shutil.rmtree(latest_output_dir)
        latest_output_dir.mkdir(parents=True, exist_ok=True)
        return latest_output_dir

    @staticmethod
    def create_workflow_dirs(
        project_name: str,
    ) -> tuple[Path, Path]:
        """Create workflow latest and archive directory paths for one project.

        This method:

        - resolves the project's workflow `latest` and `archive` directory paths
        - ensures the archive directory exists on disk
        """

        latest_output_dir: Path
        archive_dir: Path
        latest_output_dir, archive_dir = settings.workflow_snapshot_paths(project_name)
        archive_dir = LogSnapshotService.create_dir(archive_dir)
        return latest_output_dir, archive_dir

    def archive_workflow_latest(
        self,
        *,
        project_name: str,
        latest_output_dir: Path,
        archive_dir: Path,
    ) -> None:
        """Archive the current workflow `latest` directory when it contains files.

        This method:

        - returns immediately when `latest` is missing or empty
        - reads the current `latest` artifact from workflow inventory
        - removes stray `latest` content when inventory has no matching latest artifact
        - moves `latest` into `archive/<archive_name>`
        - updates `workflow_inventory.json`
        """
        # 1) Checks if latest have files
        existing_snapshot_paths: list[Path] = (
            list(latest_output_dir.glob("*")) if latest_output_dir.exists() else []
        )
        if not existing_snapshot_paths:
            return

        # 2) read inventory file to get paths
        inventory = self._read_workflow_inventory(project_name)
        if inventory is None or inventory.latest is None:
            # Files exist in `latest`, but the workflow inventory has no matching latest artifact.
            # Treat that directory as stray state, remove it, and skip archiving.
            shutil.rmtree(latest_output_dir)
            return

        # 3) Get latest object from json file
        latest_artifact = inventory.latest

        # 4) prepare archive files..
        archive_name = self._format_archive_name(latest_artifact.collected_at)
        archive_snapshot_dir: Path = archive_dir / archive_name

        # 5) remove archive if exists
        if archive_snapshot_dir.exists():
            shutil.rmtree(archive_snapshot_dir)

        # 6) move files from latest to archive folder
        shutil.move(str(latest_output_dir), archive_snapshot_dir)

        # 7) clearing object 'latest'
        inventory.latest = None

        # 8) Updating archive object
        inventory.archives = [
            item for item in inventory.archives if item.archive_name != archive_name
        ]
        inventory.archives.insert(
            0,
            WorkflowArtifactMetadata(
                archive_name=archive_name,
                snapshot_dir=self._logs_relative_path(archive_snapshot_dir),
                collected_at=latest_artifact.collected_at,
                files=[
                    item.model_copy(
                        update={
                            "output_file": self._logs_relative_path(
                                archive_snapshot_dir / item.file_name
                            )
                        }
                    )
                    for item in latest_artifact.files
                ],
            ),
        )

        # 9) update inventory file
        self.update_workflow_inventory_file(project_name=project_name, inventory=inventory)

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

        sessions_root_dir = settings.session_path
        new_session_dir = self.create_dir(sessions_root_dir / session_id)
        cleanup_old_snapshot_dirs(
            sessions_root_dir,
            retention=parse_snapshot_retention(settings.LOG_SNAPSHOT_RETENTION),
        )
        project_session_dir = new_session_dir / project_name
        if project_session_dir.exists():
            shutil.rmtree(project_session_dir)
        project_session_dir.mkdir(parents=True, exist_ok=True)
        return project_session_dir

    @staticmethod
    def create_dir(path: Path) -> Path:
        """Ensure one directory exists on disk and return it."""

        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _format_archive_name(collected_at: str) -> str:
        """Convert one collected-at timestamp into the workflow archive folder name."""

        try:
            return (
                datetime.fromisoformat(collected_at.replace("Z", "+00:00"))
                .astimezone(UTC)
                .strftime("%Y-%m-%dT%H-%M-%SZ")
            )
        except ValueError:
            return datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")

    @staticmethod
    def _workflow_inventory_file(project_name: str) -> Path:
        """Return the per-project workflow inventory file path."""

        return settings.workflow_project_path(project_name) / WORKFLOW_INVENTORY_FILE_NAME

    def _read_workflow_inventory(self, project_name: str) -> WorkflowProjectInventory | None:
        """Return the persisted workflow inventory for one project when it exists."""

        inventory_file = self._workflow_inventory_file(project_name)
        if not inventory_file.exists():
            return None
        return read_workflow_inventory(settings.workflow_project_path(project_name))

    def write_workflow_latest(
        self,
        *,
        project_name: str,
        latest_artifact: WorkflowArtifactMetadata,
    ) -> None:
        """Persist the current workflow latest artifact in the project inventory."""

        inventory = self._read_workflow_inventory(project_name)
        if inventory is None:
            inventory = WorkflowProjectInventory(
                project_name=project_name,
                latest=None,
                archives=[],
            )
        inventory.latest = latest_artifact
        self.update_workflow_inventory_file(
            project_name=project_name,
            inventory=inventory,
        )

    def update_workflow_inventory_file(
        self,
        *,
        project_name: str,
        inventory: WorkflowProjectInventory,
    ) -> None:
        """Overwrite `workflow_inventory.json` for one project.

        The inventory file is the workflow source of truth for latest and
        archive metadata, so this method intentionally writes the whole model
        atomically from the caller's current in-memory inventory object.
        """

        inventory_file = self._workflow_inventory_file(project_name)
        inventory_file.parent.mkdir(parents=True, exist_ok=True)
        inventory_file.write_text(
            json.dumps(inventory.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    def _build_workflow_artifact_metadata(
        self,
        *,
        snapshot_dir: Path,
        archive_name: str | None,
        collected_at: str,
        files: list[LogSnapshotFilePayload],
    ) -> WorkflowArtifactMetadata:
        """Build one workflow artifact inventory row from collected source results."""

        return WorkflowArtifactMetadata(
            archive_name=archive_name,
            snapshot_dir=self._logs_relative_path(snapshot_dir),
            collected_at=collected_at,
            files=files,
        )

    @staticmethod
    def _logs_relative_path(path: Path) -> str:
        """Return one metadata path relative to the configured logs root."""

        try:
            return path.resolve().relative_to(settings.LOGS_DIR.resolve()).as_posix()
        except ValueError as error:
            raise ValueError("Snapshot paths must live under LOGS_DIR.") from error

    @staticmethod
    def _logs_absolute_path(relative_path: str) -> Path:
        """Resolve one metadata path under the configured logs root."""

        normalized_path = Path(relative_path)
        if normalized_path.is_absolute() or ".." in normalized_path.parts:
            raise ValueError("Snapshot paths must be relative to LOGS_DIR.")
        return settings.LOGS_DIR / normalized_path

    def _files_with_relative_output_paths(
        self,
        files: list[LogSnapshotFilePayload],
    ) -> list[LogSnapshotFilePayload]:
        """Return file metadata with one relative path as the path source of truth."""

        return [
            item.model_copy(
                update={"output_file": self._logs_relative_path(Path(item.output_file))}
            )
            for item in files
        ]

    def _get_workflow_inventory_entry(
        self,
        project_name: str,
        archive_name: str | None,
    ) -> WorkflowArtifactMetadata:
        """Return one workflow inventory entry for latest or one archive."""

        inventory = self._read_workflow_inventory(project_name)
        if inventory is None:
            raise ValueError("Requested workflow log snapshot was not found.")
        if archive_name is None:
            if inventory.latest is None:
                raise ValueError("Requested workflow log snapshot was not found.")
            return inventory.latest
        for artifact in inventory.archives:
            if artifact.archive_name == archive_name:
                return artifact
        raise ValueError("Requested workflow log snapshot was not found.")
