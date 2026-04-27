"""Snapshot lifecycle services for persisted log collection artifacts."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastmcp.server.auth import AccessToken

from settings import Settings
from tools.models import (
    CollectedSourcePayload,
    GrepLogSnapshotMatchPayload,
    LogSnapshotMetadata,
    SnapshotWorkspace,
)
from tools.utils import (
    COLLECTED_AT_FILE_NAME,
    SNAPSHOT_ID_FILE_NAME,
    SNAPSHOT_METADATA_FILE_NAME,
    cleanup_old_snapshot_dirs,
    generate_snapshot_id,
    load_authorized_project_manifest,
    parse_snapshot_retention,
    rewrite_snapshot_metadata_output_paths,
)
from utils.log_snapshots import (
    build_snapshot_file_payloads,
    read_snapshot_metadata,
    resolve_snapshot_dir,
    resolve_snapshot_file_path,
)

MAX_GREP_MATCHES = 500
DEFAULT_GREP_MATCH_LIMIT = 100
MAX_GREP_MATCH_LINE_BYTES = 2000


@dataclass(slots=True)
class AuthorizedSnapshotContext:
    """Resolved snapshot context for one authorized workflow or session request."""

    authorized_project_name: str
    effective_project_name: str
    snapshot_dir: Path
    metadata: LogSnapshotMetadata


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
    collect raw logs itself. Raw file/docker collection belongs to
    `LogSourceCollectionService`, while request normalization and final
    agent-facing payload assembly belong to `LogCollectionService`.
    """

    def __init__(self, settings: Settings, access_token: AccessToken) -> None:
        self.settings = settings
        self.access_token = access_token

    def prepare_workspace(
        self,
        *,
        effective_project_name: str,
        workspace: SnapshotWorkspace,
        session_id: str | None,
    ) -> tuple[Path, Path, str, str | None, str | None]:
        """Prepare one workflow or session snapshot workspace on disk."""

        if workspace == "workflow":
            (
                project_output_path,
                latest_output_path,
                archive_path,
                snapshot_id,
            ) = self._prepare_workflow_snapshot_dirs(effective_project_name)
            return (
                project_output_path,
                latest_output_path,
                snapshot_id,
                str(latest_output_path),
                str(archive_path),
            )

        if not session_id or not session_id.strip():
            raise ValueError(
                "session_id is required when workspace='session'. Reuse the same session_id "
                "for follow-up collection calls in the same agent session."
            )
        project_output_path, snapshot_output_path, snapshot_id = self._prepare_session_snapshot_dir(
            effective_project_name,
            session_id.strip(),
        )
        return project_output_path, snapshot_output_path, snapshot_id, None, None

    def persist_outputs(
        self,
        snapshot_dir: Path,
        *,
        project_name: str,
        workspace: SnapshotWorkspace,
        snapshot_id: str,
        collected_sources: list[CollectedSourcePayload],
    ) -> tuple[str, str, str]:
        """Write collected source files and metadata into one snapshot directory."""

        collected_at = datetime.now(UTC).isoformat()

        for source in collected_sources:
            if source.status != "collected":
                continue
            output_file = snapshot_dir / f"{source.source_key}.log"
            output_file.write_text(source.content, encoding="utf-8")
            source.output_file = str(output_file)

        collected_at_file = snapshot_dir / COLLECTED_AT_FILE_NAME
        collected_at_file.write_text(collected_at, encoding="utf-8")

        snapshot_id_file = snapshot_dir / SNAPSHOT_ID_FILE_NAME
        snapshot_id_file.write_text(snapshot_id, encoding="utf-8")

        metadata_file = snapshot_dir / SNAPSHOT_METADATA_FILE_NAME
        metadata = LogSnapshotMetadata(
            project_name=project_name,
            workspace=workspace,
            snapshot_id=snapshot_id,
            collected_at=collected_at,
            files=build_snapshot_file_payloads(collected_sources),
        )
        metadata_file.write_text(
            json.dumps(metadata.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        return collected_at, str(collected_at_file), str(metadata_file)

    def load_authorized_snapshot_context(
        self,
        requested_project_name: str | None,
        workspace: SnapshotWorkspace,
        snapshot_id: str,
    ) -> AuthorizedSnapshotContext:
        """Authorize the caller and load one persisted workflow or session snapshot."""

        _, authorized_project_name, effective_project_name = load_authorized_project_manifest(
            self.settings,
            self.access_token,
            requested_project_name,
        )
        project_output_dir = self.settings.LOGS_DIR / effective_project_name
        snapshot_dir = resolve_snapshot_dir(project_output_dir, workspace, snapshot_id)
        metadata = read_snapshot_metadata(snapshot_dir)
        return AuthorizedSnapshotContext(
            authorized_project_name=authorized_project_name,
            effective_project_name=effective_project_name,
            snapshot_dir=snapshot_dir,
            metadata=metadata,
        )

    def grep_snapshot(
        self,
        snapshot_dir: Path,
        metadata: LogSnapshotMetadata,
        *,
        grep: str,
        source_keys: list[str] | None,
        match_offset: int,
        match_limit: int,
    ) -> tuple[list[GrepLogSnapshotMatchPayload], int]:
        """Search persisted snapshot files with a controlled grep invocation.

        The returned tuple contains:

        - the requested page of line matches
        - the total number of grep matches across the selected snapshot files
        """

        if source_keys:
            available_source_keys = {item.source_key for item in metadata.files}
            unknown_source_keys = sorted(set(source_keys) - available_source_keys)
            if unknown_source_keys:
                raise ValueError(
                    "Requested log snapshot source_keys were not found: "
                    + ", ".join(unknown_source_keys)
                )

        selected_files = (
            [item for item in metadata.files if item.source_key in set(source_keys or [])]
            if source_keys
            else list(metadata.files)
        )
        if not selected_files:
            return [], 0

        file_path_to_source_key = {
            str(resolve_snapshot_file_path(snapshot_dir, item)): item.source_key
            for item in selected_files
        }
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
            raise ValueError(error_output)
        if completed.returncode == 1:
            return [], 0

        all_match_lines = completed.stdout.splitlines()
        total_match_count = len(all_match_lines)
        selected_match_lines = all_match_lines[match_offset : match_offset + match_limit]

        matches: list[GrepLogSnapshotMatchPayload] = []
        for raw_line in selected_match_lines:
            file_path, line_number, line = raw_line.split(":", 2)
            encoded_line = line.encode("utf-8")
            line_truncated = len(encoded_line) > MAX_GREP_MATCH_LINE_BYTES
            if line_truncated:
                line = encoded_line[:MAX_GREP_MATCH_LINE_BYTES].decode(
                    "utf-8",
                    errors="ignore",
                )
            matches.append(
                GrepLogSnapshotMatchPayload(
                    source_key=file_path_to_source_key[file_path],
                    output_file=file_path,
                    line_number=int(line_number),
                    line=line,
                    line_truncated=line_truncated,
                )
            )
        return matches, total_match_count

    def _prepare_workflow_snapshot_dirs(
        self,
        project_key: str,
    ) -> tuple[Path, Path, Path, str]:
        """Prepare the workflow workspace and archive the previous latest snapshot."""

        project_output_dir = self.settings.LOGS_DIR / project_key
        workflow_root_dir = project_output_dir / "workflow"
        latest_output_dir = workflow_root_dir / "latest"
        archive_dir = workflow_root_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        cleanup_old_snapshot_dirs(
            archive_dir,
            retention=parse_snapshot_retention(self.settings.WORKFLOW_ARCHIVE_RETENTION),
        )

        existing_snapshot_paths = (
            list(latest_output_dir.glob("*")) if latest_output_dir.exists() else []
        )
        if existing_snapshot_paths:
            previous_snapshot_id = None
            previous_snapshot_id_file = latest_output_dir / SNAPSHOT_ID_FILE_NAME
            if previous_snapshot_id_file.exists():
                previous_snapshot_id = previous_snapshot_id_file.read_text(encoding="utf-8").strip()
            archive_snapshot_id = previous_snapshot_id or generate_snapshot_id("workflow")
            archive_snapshot_dir = archive_dir / archive_snapshot_id
            if archive_snapshot_dir.exists():
                shutil.rmtree(archive_snapshot_dir)
            shutil.move(str(latest_output_dir), archive_snapshot_dir)
            rewrite_snapshot_metadata_output_paths(archive_snapshot_dir)

        latest_output_dir.mkdir(parents=True, exist_ok=True)
        snapshot_id = generate_snapshot_id("workflow")
        return project_output_dir, latest_output_dir, archive_dir, snapshot_id

    def _prepare_session_snapshot_dir(
        self,
        project_key: str,
        session_id: str,
    ) -> tuple[Path, Path, str]:
        """Prepare one caller-owned session workspace directory."""

        project_output_dir = self.settings.LOGS_DIR / project_key
        sessions_root_dir = project_output_dir / "sessions"
        sessions_root_dir.mkdir(parents=True, exist_ok=True)
        cleanup_old_snapshot_dirs(
            sessions_root_dir,
            retention=parse_snapshot_retention(self.settings.LOG_SNAPSHOT_RETENTION),
        )
        snapshot_dir = sessions_root_dir / session_id
        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        return project_output_dir, snapshot_dir, session_id
