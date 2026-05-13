"""Postgres integration tests for database service modules."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from conf import settings
from database.fields import FileReference
from database.models import AgentCall, CollectLogs, CollectLogsSource, ProjectManifest
from database.schemas import (
    AgentCallCreate,
    AgentCallFilter,
    AgentCallUpdate,
    ProjectManifestCreate,
)
from database.services.agent_calls import AgentCallService
from database.services.project_manifests import ProjectManifestService
from database.types import CollectLogsSourceStatus, LogSourceType, LogStream, LogWorkspace
from manifests.loader import load_project_manifest
from manifests.models import Manifest, SourceDefinition
from services.log_collection import BuildLogsError, LogCollectionService
from services.project_manifest import ProjectManifestService as RuntimeProjectManifestService
from tests.conftest import override_settings


@pytest.mark.anyio
async def test_database_services_round_trip_against_real_postgres() -> None:
    agent_calls = AgentCallService()
    project_manifests = ProjectManifestService()
    suffix = uuid4().hex
    project_key = f"integration-{suffix}"
    session_id = uuid4()

    assert agent_calls.model is AgentCall
    assert project_manifests.model is ProjectManifest

    manifest = Manifest(
        project_key=project_key,
        project_summary="Integration manifest.",
        static_asset_paths=["/static/"],
        static_asset_extensions=[".css"],
        sources=[
            SourceDefinition(
                source_key="backend",
                source_type="docker",
                target="integration-backend",
                description="Backend integration logs.",
                parser_type="python_json",
                normalization_profile="backend_app",
                retention_class="hot",
            )
        ],
    )

    stored_manifest = await project_manifests.create(
        ProjectManifestCreate(
            project_key=manifest.project_key,
            project_summary=manifest.project_summary,
            static_asset_paths=manifest.static_asset_paths,
            static_asset_extensions=manifest.static_asset_extensions,
            sources=[source.model_dump(mode="json") for source in manifest.sources],
        )
    )
    created_call = await agent_calls.create(
        AgentCallCreate(
            session_id=session_id,
            workspace="workflow",
            event="mcp_call_tool",
            client_id="integration-client",
            client_type="agent",
            tool_name="collect_logs",
            duration_seconds=25.5,
            project_name=project_key,
            source_keys=["backend"],
            arguments={"tail_lines": 50},
        )
    )

    fetched_manifest = await project_manifests.get(project_key)
    fetched_call = await agent_calls.get(created_call.id)
    calls_for_session = await agent_calls.filter(AgentCallFilter(session_id=session_id))
    ended_call = await agent_calls.update(AgentCallUpdate(pk=created_call.id, session_ended=True))

    assert fetched_manifest.id == stored_manifest.id
    assert fetched_manifest.project_key == project_key
    assert fetched_manifest.sources == [
        source.model_dump(mode="json") for source in manifest.sources
    ]
    assert fetched_call.id == created_call.id
    assert fetched_call.project_name == project_key
    assert fetched_call.source_keys == ["backend"]
    assert fetched_call.arguments == {"tail_lines": 50}
    assert [row.id for row in calls_for_session] == [created_call.id]
    assert ended_call.session_ended is True


@pytest.mark.anyio
async def test_collect_logs_models_round_trip_against_real_postgres(
    tmp_path: Path,
) -> None:
    suffix = uuid4().hex
    session_id = uuid4()
    snapshot_dir = tmp_path / "sessions" / str(session_id) / f"integration-{suffix}"
    snapshot_dir.mkdir(parents=True)
    metadata_file = snapshot_dir / "snapshot_metadata.json"
    source_file = snapshot_dir / "backend.log"
    metadata_file.write_text("{}", encoding="utf-8")
    source_file.write_text("line 1\nline 2\n", encoding="utf-8")

    collect_logs = await CollectLogs.objects.create(
        session_id=session_id,
        workspace=LogWorkspace.SESSION,
        project_name=f"integration-{suffix}",
        collected_at=datetime(2026, 5, 9, 12, 30, tzinfo=UTC),
        snapshot_dir=snapshot_dir.as_posix(),
        metadata_file=metadata_file,
        requested_source_keys=["backend", "nginx"],
        resolved_source_keys=["backend"],
        unknown_requested_source_keys=["nginx"],
        requested_since="1h",
        requested_until=None,
        warnings=["nginx was not found."],
        retry_tips=["Retry with valid source keys."],
    )
    collected_source = await CollectLogsSource.objects.create(
        collect_logs=collect_logs,
        source_key="backend",
        source_type=LogSourceType.DOCKER,
        target="integration-backend",
        description="Backend integration logs.",
        stream=LogStream.STDOUT,
        parser_type="python_json",
        normalization_profile="backend_app",
        default_noise_profile="backend_noise",
        status=CollectLogsSourceStatus.COLLECTED,
        file=source_file,
        line_count=2,
        retry_tips=[],
    )
    unavailable_source = await CollectLogsSource.objects.create(
        collect_logs=collect_logs,
        source_key="nginx",
        source_type=LogSourceType.FILE,
        target="/var/log/nginx/access.log",
        description="Nginx access logs.",
        stream=None,
        parser_type=None,
        normalization_profile=None,
        default_noise_profile=None,
        status=CollectLogsSourceStatus.UNAVAILABLE,
        file=None,
        line_count=0,
        error="Source file was not available.",
        retry_tips=["Check the configured source path."],
    )

    fetched_snapshot = await CollectLogs.objects.get(id=collect_logs.id)
    fetched_sources = await fetched_snapshot.sources.all()

    assert fetched_snapshot.session_id == session_id
    assert fetched_snapshot.workspace == LogWorkspace.SESSION
    assert fetched_snapshot.project_name == f"integration-{suffix}"
    assert fetched_snapshot.snapshot_dir == snapshot_dir.as_posix()
    assert fetched_snapshot.metadata_file == FileReference(
        name=metadata_file.as_posix(),
        size_bytes=metadata_file.stat().st_size,
    )
    assert fetched_snapshot.metadata_file.name == metadata_file.as_posix()
    assert fetched_snapshot.metadata_file.path == metadata_file.as_posix()
    assert fetched_snapshot.metadata_file.size == metadata_file.stat().st_size
    assert fetched_snapshot.requested_source_keys == ["backend", "nginx"]
    assert fetched_snapshot.resolved_source_keys == ["backend"]
    assert fetched_snapshot.unknown_requested_source_keys == ["nginx"]
    assert fetched_snapshot.warnings == ["nginx was not found."]
    assert fetched_snapshot.retry_tips == ["Retry with valid source keys."]

    assert [source.id for source in fetched_sources] == [
        collected_source.id,
        unavailable_source.id,
    ]
    assert fetched_sources[0].source_key == "backend"
    assert fetched_sources[0].source_type == LogSourceType.DOCKER
    assert fetched_sources[0].stream == LogStream.STDOUT
    assert fetched_sources[0].status == CollectLogsSourceStatus.COLLECTED
    assert fetched_sources[0].file.name == source_file.as_posix()
    assert fetched_sources[0].file.path == source_file.as_posix()
    assert fetched_sources[0].file.size == source_file.stat().st_size
    assert fetched_sources[0].line_count == 2
    assert cast(object, fetched_sources[0].error) is None
    assert fetched_sources[0].retry_tips == []

    assert fetched_sources[1].source_key == "nginx"
    assert fetched_sources[1].source_type == LogSourceType.FILE
    assert cast(object, fetched_sources[1].stream) is None
    assert fetched_sources[1].status == CollectLogsSourceStatus.UNAVAILABLE
    assert cast(object, fetched_sources[1].file) is None
    assert fetched_sources[1].line_count == 0
    assert fetched_sources[1].error == "Source file was not available."
    assert fetched_sources[1].retry_tips == ["Check the configured source path."]
    assert CollectLogsSource._meta.ordering[0][0] == "id"


@pytest.mark.anyio
async def test_log_collection_service_persists_collect_logs_metadata(
    tmp_path: Path,
) -> None:
    """Verify collect_logs orchestration writes artifact and source rows."""

    logs_dir = tmp_path / "collected-logs"
    manifest = load_project_manifest(settings.manifests_dir, "landingpage")
    manifest_sources = RuntimeProjectManifestService.get_manifest_source_keys(
        manifest,
        ["app_file", "missing"],
    )

    with override_settings(LOGS_DIR=logs_dir):
        payload = await LogCollectionService().build_logs(
            manifest=manifest,
            sources=manifest_sources.sources,
            missing_source_keys=manifest_sources.missing_source_keys,
            source_keys=manifest_sources.source_keys,
            workspace="workflow",
            session_id=None,
            since="5m",
            until=None,
        )
        assert not isinstance(payload, BuildLogsError)
        rows = await CollectLogs.objects.filter(project_name="landingpage")
        assert len(rows) == 1
        collect_logs = rows[0]
        sources = await CollectLogsSource.objects.filter(collect_logs=collect_logs)

        assert collect_logs.workspace == LogWorkspace.WORKFLOW
        assert collect_logs.session_id is None
        assert collect_logs.is_latest is True
        assert collect_logs.requested_source_keys == ["app_file", "missing"]
        assert collect_logs.resolved_source_keys == ["app_file"]
        assert collect_logs.unknown_requested_source_keys == ["missing"]
        assert collect_logs.requested_since == "5m"
        assert collect_logs.requested_until is None
        assert collect_logs.snapshot_dir == payload.snapshot_dir
        assert collect_logs.metadata_file.path == payload.metadata_file
        assert collect_logs.metadata_file.size > 0

        assert len(sources) == 1
        assert sources[0].source_key == "app_file"
        assert sources[0].status == CollectLogsSourceStatus.COLLECTED
        assert sources[0].file is not None
        assert sources[0].file.name == "workflow/landingpage/latest/app_file.log"
        assert sources[0].file.url == "workflow/landingpage/latest/app_file.log"
        assert sources[0].line_count > 0


@pytest.mark.anyio
async def test_log_collection_service_persists_session_source_file_path(
    tmp_path: Path,
) -> None:
    """Verify session collect_logs source files keep logs-root-relative DB paths."""

    logs_dir = tmp_path / "collected-logs"
    session_id = uuid4()
    manifest = load_project_manifest(settings.manifests_dir, "landingpage")
    manifest_sources = RuntimeProjectManifestService.get_manifest_source_keys(
        manifest,
        ["app_file"],
    )

    with override_settings(LOGS_DIR=logs_dir):
        payload = await LogCollectionService().build_logs(
            manifest=manifest,
            sources=manifest_sources.sources,
            missing_source_keys=manifest_sources.missing_source_keys,
            source_keys=manifest_sources.source_keys,
            workspace="session",
            session_id=str(session_id),
            since="5m",
            until=None,
        )
        assert not isinstance(payload, BuildLogsError)
        collect_logs = await CollectLogs.objects.get(project_name="landingpage")
        sources = await CollectLogsSource.objects.filter(collect_logs=collect_logs)

        assert collect_logs.workspace == LogWorkspace.SESSION
        assert collect_logs.session_id == session_id
        assert len(sources) == 1
        assert sources[0].file is not None
        assert sources[0].file.name == f"sessions/{session_id}/landingpage/app_file.log"
        assert sources[0].file.url == f"sessions/{session_id}/landingpage/app_file.log"
