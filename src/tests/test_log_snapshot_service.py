from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import pytest

from core.types import LogWorkspace
from database.fields import FileReference
from database.fields import FileStorage as DBFileStorage
from database.schemas import CollectLogsSourceOut, CollectLogsWithSourcesOut
from database.services.collect_logs import CollectLogsService as CollectLogsDBService
from exception import MissingSessionIdError
from services.log_snapshots import (
    LogSnapshotService,
    SnapshotContext,
    SnapshotGrepError,
    SnapshotLookupError,
)
from storage import LogFileStorage
from tests.conftest import override_settings
from tests.factories import AgentSessionFactory
from tools import utils as tool_utils
from tools.models import LogSnapshotFilePayload, LogSnapshotMetadata
from utils import log_snapshots


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


def build_collect_logs_source(
    *,
    source_id: int,
    source_key: str,
    file_name: str | None,
    storage_root: Path,
    source_type: Literal["docker", "file"] = "docker",
    description: str = "Backend logs.",
    target: str = "backend-container",
) -> CollectLogsSourceOut:
    """Create one collected source contract for snapshot-service tests."""

    return CollectLogsSourceOut(
        id=source_id,
        source_key=source_key,
        source_type=source_type,
        target=target,
        description=description,
        stream="stdout",
        parser_type="python_json",
        normalization_profile="backend_app",
        default_noise_profile="backend_noise",
        status="collected" if file_name is not None else "unavailable",
        file=(
            FileReference(
                name=file_name,
                storage=DBFileStorage(location=storage_root),
            )
            if file_name is not None
            else None
        ),
        line_count=2 if file_name is not None else 0,
        error=None if file_name is not None else "source unavailable",
        retry_tips=[],
    )


class FakeCollectLogsDBService:
    """Small fake DB service for snapshot lookup tests."""

    def __init__(self, collect_logs: CollectLogsWithSourcesOut | None) -> None:
        self.collect_logs = collect_logs

    async def get_session_collect_logs_with_sources(
        self,
        *,
        project_name: str,
        session_id: str,
    ) -> CollectLogsWithSourcesOut | None:
        if self.collect_logs is None:
            return None
        if (
            self.collect_logs.project_name == project_name
            and str(self.collect_logs.session_id) == session_id
        ):
            return self.collect_logs
        return None


def test_old_snapshot_metadata_file_readers_are_not_exposed() -> None:
    assert not hasattr(log_snapshots, "read_snapshot_metadata")
    assert not hasattr(tool_utils, "load_snapshot_metadata_from_json")


def test_prepare_workspace_requires_session_id_for_session_workspace(tmp_path) -> None:
    service = LogSnapshotService()

    with override_settings(LOGS_DIR=tmp_path):
        with pytest.raises(MissingSessionIdError, match="session_id is required"):
            service.prepare_workspace(
                project_name="landingpage",
                workspace=LogWorkspace.SESSION,
                session_id=None,
            )


def test_prepare_workspace_workflow_creates_latest_and_archive_dirs(tmp_path) -> None:
    service = LogSnapshotService()

    with override_settings(LOGS_DIR=tmp_path):
        snapshot_dir = service.prepare_workspace(
            project_name="landingpage",
            workspace=LogWorkspace.WORKFLOW,
            session_id=None,
            snapshot_dir="workflow/landingpage/latest",
        )

    assert snapshot_dir == tmp_path / "workflow" / "landingpage" / "latest"
    assert snapshot_dir.exists()
    assert (tmp_path / "workflow" / "landingpage" / "archive").exists()


@pytest.mark.anyio
async def test_load_session_snapshot_returns_context_from_db_contract(tmp_path) -> None:
    session = AgentSessionFactory.build()
    session_id = session.name
    source_file = tmp_path / "sessions" / session_id / "landingpage" / "backend.log"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("one\ntwo\n", encoding="utf-8")
    collect_logs = CollectLogsWithSourcesOut(
        id=1,
        session_id=session_id,
        workspace=LogWorkspace.SESSION,
        caller_id=1,
        project_name="landingpage",
        collected_at=datetime(2026, 5, 14, 12, 0, tzinfo=UTC),
        snapshot_dir=f"sessions/{session_id}/landingpage",
        archive_name=None,
        is_latest=False,
        requested_source_keys=["backend"],
        resolved_source_keys=["backend"],
        unknown_requested_source_keys=[],
        requested_since="1h",
        requested_until=None,
        warnings=[],
        retry_tips=[],
        sources=[
            build_collect_logs_source(
                source_id=1,
                source_key="backend",
                file_name=f"sessions/{session_id}/landingpage/backend.log",
                storage_root=tmp_path,
            ),
            build_collect_logs_source(
                source_id=2,
                source_key="missing",
                file_name=None,
                storage_root=tmp_path,
            ),
        ],
    )
    service = LogSnapshotService(
        collect_logs_db_service=cast(
            CollectLogsDBService,
            FakeCollectLogsDBService(collect_logs),
        ),
        storage=LogFileStorage(root=tmp_path),
    )

    result = await service.load_session_snapshot(
        project_name="landingpage",
        session_id=session_id,
    )

    assert isinstance(result, SnapshotContext)
    assert result.project_name == "landingpage"
    assert result.caller_id == 1
    assert result.snapshot_dir == tmp_path / "sessions" / session_id / "landingpage"
    assert result.metadata.project_name == "landingpage"
    assert result.metadata.workspace == "session"
    assert result.metadata.session_id == session_id
    assert result.metadata.files[0].source_key == "backend"
    assert result.metadata.files[0].output_file == (
        f"sessions/{session_id}/landingpage/backend.log"
    )
    assert result.sources == collect_logs.sources


@pytest.mark.anyio
async def test_load_session_snapshot_returns_lookup_error_when_db_object_missing() -> None:
    service = LogSnapshotService(
        collect_logs_db_service=cast(
            CollectLogsDBService,
            FakeCollectLogsDBService(None),
        ),
    )

    result = await service.load_session_snapshot(
        project_name="landingpage",
        session_id="gentle-river-finds-a8f2",
    )

    assert isinstance(result, SnapshotLookupError)
    assert result.error_code == "snapshot_not_found"
    assert result.message == "Requested session log snapshot was not found."


def test_prepare_workspace_uses_injected_storage_root(tmp_path) -> None:
    service = LogSnapshotService(storage=LogFileStorage(root=tmp_path))

    snapshot_dir = service.prepare_workspace(
        project_name="landingpage",
        workspace=LogWorkspace.WORKFLOW,
        session_id=None,
        snapshot_dir="workflow/landingpage/latest",
    )

    assert snapshot_dir == tmp_path / "workflow" / "landingpage" / "latest"
    assert (tmp_path / "workflow" / "landingpage" / "archive").exists()


def test_prepare_workspace_replaces_workflow_latest_without_inventory(tmp_path) -> None:
    service = LogSnapshotService()
    latest_dir = tmp_path / "workflow" / "landingpage" / "latest"
    archive_dir = tmp_path / "workflow" / "landingpage" / "archive"
    latest_dir.mkdir(parents=True)
    archive_dir.mkdir(parents=True)
    (latest_dir / "backend.log").write_text("one\ntwo\n", encoding="utf-8")

    with override_settings(LOGS_DIR=tmp_path):
        snapshot_dir = service.prepare_workspace(
            project_name="landingpage",
            workspace=LogWorkspace.WORKFLOW,
            session_id=None,
            snapshot_dir="workflow/landingpage/latest",
        )

    assert snapshot_dir == latest_dir
    assert latest_dir.exists()
    assert not (latest_dir / "backend.log").exists()
    assert archive_dir.exists()


def test_grep_snapshot_returns_error_for_missing_db_source_file(tmp_path) -> None:
    service = LogSnapshotService()
    missing_file = tmp_path / "workflow" / "landingpage" / "latest" / "backend.log"
    metadata = LogSnapshotMetadata(
        project_name="landingpage",
        workspace=LogWorkspace.WORKFLOW,
        collected_at="2026-05-06T10:00:00+00:00",
        files=[build_file_payload(missing_file)],
    )
    context = SnapshotContext(
        project_name="landingpage",
        caller_id=1,
        snapshot_dir=missing_file.parent,
        metadata=metadata,
        sources=[
            CollectLogsSourceOut(
                id=1,
                source_key="backend",
                source_type="docker",
                target="backend-container",
                description="Backend logs.",
                stream="stdout",
                parser_type="python_json",
                normalization_profile="backend_app",
                default_noise_profile="backend_noise",
                status="collected",
                file=FileReference(
                    name="workflow/landingpage/latest/backend.log",
                    storage=DBFileStorage(location=tmp_path),
                ),
                line_count=2,
                error=None,
                retry_tips=[],
            )
        ],
    )

    with override_settings(LOGS_DIR=tmp_path):
        result = service.grep_snapshot(
            context,
            grep="error",
            source_keys=["backend"],
            match_offset=0,
            max_matches=10,
        )

    assert isinstance(result, SnapshotGrepError)
    assert result.error_code == "snapshot_file_not_found"
    assert result.message == "Requested log snapshot file was not found on disk."


def test_grep_snapshot_returns_matches_from_db_source_files(tmp_path) -> None:
    service = LogSnapshotService()
    backend_file = tmp_path / "workflow" / "landingpage" / "latest" / "backend.log"
    nginx_file = tmp_path / "workflow" / "landingpage" / "latest" / "nginx.log"
    backend_file.parent.mkdir(parents=True)
    backend_file.write_text("INFO boot\nERROR backend failed\n", encoding="utf-8")
    nginx_file.write_text("ERROR gateway failed\nINFO ok\n", encoding="utf-8")
    sources = [
        build_collect_logs_source(
            source_id=1,
            source_key="backend",
            file_name="workflow/landingpage/latest/backend.log",
            storage_root=tmp_path,
        ),
        build_collect_logs_source(
            source_id=2,
            source_key="nginx",
            file_name="workflow/landingpage/latest/nginx.log",
            storage_root=tmp_path,
        ),
    ]
    metadata = LogSnapshotMetadata(
        project_name="landingpage",
        workspace=LogWorkspace.WORKFLOW,
        collected_at="2026-05-06T10:00:00+00:00",
        files=[
            service.source_to_file_payload(source) for source in sources if source.file is not None
        ],
    )
    context = SnapshotContext(
        project_name="landingpage",
        caller_id=1,
        snapshot_dir=backend_file.parent,
        metadata=metadata,
        sources=sources,
    )

    result = service.grep_snapshot(
        context,
        grep="ERROR",
        source_keys=None,
        match_offset=0,
        max_matches=10,
    )

    assert not isinstance(result, SnapshotGrepError)
    matches, total_match_count = result
    assert total_match_count == 2
    assert [match.source_key for match in matches] == ["backend", "nginx"]
    assert [match.output_file for match in matches] == [
        "workflow/landingpage/latest/backend.log",
        "workflow/landingpage/latest/nginx.log",
    ]
    assert [match.line_number for match in matches] == [2, 1]


def test_grep_snapshot_skips_collection_diagnostics_by_default(tmp_path) -> None:
    service = LogSnapshotService()
    backend_file = tmp_path / "workflow" / "landingpage" / "latest" / "backend.log"
    diagnostics_file = (
        tmp_path / "workflow" / "landingpage" / "latest" / "collection_diagnostics.json"
    )
    backend_file.parent.mkdir(parents=True)
    backend_file.write_text("INFO boot\nERROR backend failed\n", encoding="utf-8")
    diagnostics_file.write_text('{"error": "ERROR docker socket unavailable"}\n', encoding="utf-8")
    sources = [
        build_collect_logs_source(
            source_id=1,
            source_key="backend",
            file_name="workflow/landingpage/latest/backend.log",
            storage_root=tmp_path,
        ),
        build_collect_logs_source(
            source_id=2,
            source_key="__collection_diagnostics",
            file_name="workflow/landingpage/latest/collection_diagnostics.json",
            storage_root=tmp_path,
            source_type="file",
            description="Collection diagnostics.",
            target="collection_diagnostics.json",
        ),
    ]
    metadata = LogSnapshotMetadata(
        project_name="landingpage",
        workspace=LogWorkspace.WORKFLOW,
        collected_at="2026-05-06T10:00:00+00:00",
        files=[service.source_to_file_payload(source) for source in sources],
    )
    context = SnapshotContext(
        project_name="landingpage",
        caller_id=1,
        snapshot_dir=backend_file.parent,
        metadata=metadata,
        sources=sources,
    )

    default_result = service.grep_snapshot(
        context,
        grep="ERROR",
        source_keys=None,
        match_offset=0,
        max_matches=10,
    )
    explicit_result = service.grep_snapshot(
        context,
        grep="ERROR",
        source_keys=["__collection_diagnostics"],
        match_offset=0,
        max_matches=10,
    )

    assert not isinstance(default_result, SnapshotGrepError)
    default_matches, default_total_match_count = default_result
    assert default_total_match_count == 1
    assert [match.source_key for match in default_matches] == ["backend"]
    assert not isinstance(explicit_result, SnapshotGrepError)
    explicit_matches, explicit_total_match_count = explicit_result
    assert explicit_total_match_count == 1
    assert [match.source_key for match in explicit_matches] == ["__collection_diagnostics"]


def test_grep_snapshot_supports_extended_regex_or_patterns(tmp_path) -> None:
    service = LogSnapshotService()
    backend_file = tmp_path / "workflow" / "landingpage" / "latest" / "backend.log"
    backend_file.parent.mkdir(parents=True)
    backend_file.write_text(
        "INFO boot\nBan candidate\nGET /wp-login.php\nupstream returned 502\n",
        encoding="utf-8",
    )
    source = build_collect_logs_source(
        source_id=1,
        source_key="backend",
        file_name="workflow/landingpage/latest/backend.log",
        storage_root=tmp_path,
    )
    context = SnapshotContext(
        project_name="landingpage",
        caller_id=1,
        snapshot_dir=backend_file.parent,
        metadata=LogSnapshotMetadata(
            project_name="landingpage",
            workspace=LogWorkspace.WORKFLOW,
            collected_at="2026-05-06T10:00:00+00:00",
            files=[service.source_to_file_payload(source)],
        ),
        sources=[source],
    )

    result = service.grep_snapshot(
        context,
        grep="Ban|wp-login|502",
        source_keys=["backend"],
        match_offset=0,
        max_matches=10,
    )

    assert not isinstance(result, SnapshotGrepError)
    matches, total_match_count = result
    assert total_match_count == 3
    assert [match.line for match in matches] == [
        "Ban candidate",
        "GET /wp-login.php",
        "upstream returned 502",
    ]


def test_grep_snapshot_returns_error_for_unknown_source_key(tmp_path) -> None:
    service = LogSnapshotService()
    backend_file = tmp_path / "workflow" / "landingpage" / "latest" / "backend.log"
    backend_file.parent.mkdir(parents=True)
    backend_file.write_text("ERROR backend failed\n", encoding="utf-8")
    sources = [
        build_collect_logs_source(
            source_id=1,
            source_key="backend",
            file_name="workflow/landingpage/latest/backend.log",
            storage_root=tmp_path,
        )
    ]
    context = SnapshotContext(
        project_name="landingpage",
        caller_id=1,
        snapshot_dir=backend_file.parent,
        metadata=LogSnapshotMetadata(
            project_name="landingpage",
            workspace=LogWorkspace.WORKFLOW,
            collected_at="2026-05-06T10:00:00+00:00",
            files=[service.source_to_file_payload(sources[0])],
        ),
        sources=sources,
    )

    result = service.grep_snapshot(
        context,
        grep="ERROR",
        source_keys=["missing"],
        match_offset=0,
        max_matches=10,
    )

    assert isinstance(result, SnapshotGrepError)
    assert result.error_code == "snapshot_source_key_not_found"
    assert result.message == "Requested log snapshot source_keys were not found: missing"
