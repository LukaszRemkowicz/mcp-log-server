from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.log_snapshots import LogSnapshotService, SnapshotGrepError
from tests.conftest import override_settings
from tools.models import LogSnapshotFilePayload, LogSnapshotMetadata


def build_file_payload(output_file: Path) -> LogSnapshotFilePayload:
    """Create one small persisted file payload for snapshot-service tests."""

    return LogSnapshotFilePayload(
        source_key="backend",
        source_type="docker",
        description="Backend logs.",
        target="backend-container",
        stream="stdout",
        parser_type="python_json",
        normalization_profile="backend_app",
        default_noise_profile="backend_noise",
        file_name=output_file.name,
        output_file=str(output_file),
        line_count=2,
        byte_count=12,
    )


def test_prepare_workspace_requires_session_id_for_session_workspace(tmp_path) -> None:
    service = LogSnapshotService()

    with override_settings(LOGS_DIR=tmp_path):
        with pytest.raises(ValueError, match="session_id is required"):
            service.prepare_workspace(
                project_name="landingpage",
                workspace="session",
                session_id=None,
            )


def test_prepare_workspace_workflow_creates_latest_and_archive_dirs(tmp_path) -> None:
    service = LogSnapshotService()

    with override_settings(LOGS_DIR=tmp_path):
        snapshot_dir = service.prepare_workspace(
            project_name="landingpage",
            workspace="workflow",
            session_id=None,
        )

    assert snapshot_dir == tmp_path / "workflow" / "landingpage" / "latest"
    assert snapshot_dir.exists()
    assert (tmp_path / "workflow" / "landingpage" / "archive").exists()


def test_write_metadata_files_for_session_writes_snapshot_metadata_json(tmp_path) -> None:
    service = LogSnapshotService()
    session_dir = tmp_path / "sessions" / "session-1" / "landingpage"
    session_dir.mkdir(parents=True)
    log_file = session_dir / "backend.log"
    log_file.write_text("one\ntwo\n", encoding="utf-8")
    collected_files = [build_file_payload(log_file)]

    with override_settings(LOGS_DIR=tmp_path):
        snapshot_context = service.write_metadata_files(
            session_dir,
            project_name="landingpage",
            workspace="session",
            session_id="session-1",
            collected_files=collected_files,
        )

    metadata = json.loads(snapshot_context.metadata_file.read_text(encoding="utf-8"))
    assert snapshot_context.snapshot_dir == session_dir
    assert snapshot_context.metadata_file == session_dir / "snapshot_metadata.json"
    assert metadata["project_name"] == "landingpage"
    assert metadata["workspace"] == "session"
    assert metadata["session_id"] == "session-1"
    assert metadata["files"][0]["output_file"] == "sessions/session-1/landingpage/backend.log"


def test_write_metadata_files_for_workflow_updates_inventory_latest(tmp_path) -> None:
    service = LogSnapshotService()
    latest_dir = tmp_path / "workflow" / "landingpage" / "latest"
    latest_dir.mkdir(parents=True)
    log_file = latest_dir / "backend.log"
    log_file.write_text("one\ntwo\n", encoding="utf-8")
    collected_files = [build_file_payload(log_file)]

    with override_settings(LOGS_DIR=tmp_path):
        snapshot_context = service.write_metadata_files(
            latest_dir,
            project_name="landingpage",
            workspace="workflow",
            session_id=None,
            collected_files=collected_files,
        )

    inventory = json.loads(snapshot_context.metadata_file.read_text(encoding="utf-8"))
    assert snapshot_context.metadata_file == (
        tmp_path / "workflow" / "landingpage" / "workflow_inventory.json"
    )
    assert inventory["project_name"] == "landingpage"
    assert inventory["latest"]["snapshot_dir"] == "workflow/landingpage/latest"
    assert inventory["latest"]["files"][0]["output_file"] == (
        "workflow/landingpage/latest/backend.log"
    )
    assert inventory["archives"] == []


def test_archive_workflow_latest_moves_latest_and_updates_inventory(tmp_path) -> None:
    service = LogSnapshotService()
    latest_dir = tmp_path / "workflow" / "landingpage" / "latest"
    archive_dir = tmp_path / "workflow" / "landingpage" / "archive"
    latest_dir.mkdir(parents=True)
    archive_dir.mkdir(parents=True)
    log_file = latest_dir / "backend.log"
    log_file.write_text("one\ntwo\n", encoding="utf-8")
    collected_files = [build_file_payload(log_file)]

    with override_settings(LOGS_DIR=tmp_path):
        service.write_metadata_files(
            latest_dir,
            project_name="landingpage",
            workspace="workflow",
            session_id=None,
            collected_files=collected_files,
        )
        service.archive_workflow_latest(
            project_name="landingpage",
            latest_output_dir=latest_dir,
            archive_dir=archive_dir,
        )

    inventory = json.loads(
        (tmp_path / "workflow" / "landingpage" / "workflow_inventory.json").read_text(
            encoding="utf-8"
        )
    )
    archived_dir = tmp_path / inventory["archives"][0]["snapshot_dir"]

    assert inventory["latest"] is None
    assert archived_dir.exists()
    assert (archived_dir / "backend.log").read_text(encoding="utf-8") == "one\ntwo\n"
    assert inventory["archives"][0]["files"][0]["output_file"] == (
        f"{inventory['archives'][0]['snapshot_dir']}/backend.log"
    )


def test_grep_snapshot_returns_error_for_invalid_snapshot_file_metadata(tmp_path) -> None:
    service = LogSnapshotService()
    bad_file_payload = build_file_payload(Path("/outside/logs/backend.log"))
    metadata = LogSnapshotMetadata(
        project_name="landingpage",
        workspace="workflow",
        collected_at="2026-05-06T10:00:00+00:00",
        files=[bad_file_payload],
    )

    with override_settings(LOGS_DIR=tmp_path):
        result = service.grep_snapshot(
            metadata,
            grep="error",
            source_keys=["backend"],
            match_offset=0,
            match_limit=10,
        )

    assert isinstance(result, SnapshotGrepError)
    assert result.error_code == "invalid_snapshot_file_metadata"
    assert result.message == "Requested log snapshot file metadata is invalid."
