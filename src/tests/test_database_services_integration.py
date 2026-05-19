"""Postgres integration tests for database service modules."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from core.types import LogWorkspace
from database.models import (
    AgentCall,
    AgentSession,
    CollectLogs,
    CollectLogsSource,
    McpCaller,
    ProjectManifest,
)
from database.schemas import (
    AgentCallCreate,
    AgentCallFilter,
    AgentCallUpdate,
    ProjectManifestCreate,
)
from database.services.agent_calls import AgentCallService
from database.services.project_manifests import ProjectManifestService
from database.types import (
    AgentCallEvent,
    AgentSessionStatus,
    CollectLogsSourceStatus,
    LogSourceType,
    LogStream,
)
from manifests.loader import load_project_manifest
from manifests.models import Manifest, SourceDefinition
from services.log_collection import BuildLogsError, LogCollectionService
from services.project_manifest import ProjectManifestService as RuntimeProjectManifestService
from storage import storage
from tests.conftest import TEST_MANIFESTS_DIR, override_settings, runtime_test_manifest
from tests.factories import (
    AgentSessionFactory,
    CollectLogsFactory,
    CollectLogsSourceFactory,
    McpCallerFactory,
    UnavailableCollectLogsSourceFactory,
)


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_database_services_round_trip_against_real_postgres() -> None:
    agent_calls = AgentCallService()
    project_manifests = ProjectManifestService()
    suffix = uuid4().hex
    project_key = f"integration-{suffix}"
    session_id = f"integration-session-{suffix[:4]}"

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
    caller = await McpCallerFactory.save_to_db()
    session = await AgentSessionFactory.save_to_db(
        name=session_id,
        caller=caller,
    )
    created_call = await agent_calls.create(
        AgentCallCreate(
            session_id=session.id,
            caller=caller.id,
            event="mcp_call_tool",
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
    completed_call = await agent_calls.update(
        AgentCallUpdate(pk=created_call.id, duration_seconds=30.0)
    )

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
    assert completed_call.duration_seconds == 30.0


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_collect_logs_models_round_trip_against_real_postgres() -> None:
    suffix = uuid4().hex
    session_id = f"model-session-{suffix[:4]}"
    source_file_name = f"sessions/{session_id}/test-project/backend.log"
    source_file = storage.path(source_file_name)
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("line 1\nline 2\n", encoding="utf-8")
    caller = await McpCallerFactory.save_to_db()
    agent_session = await AgentSessionFactory.save_to_db(
        name=session_id,
        caller=caller,
    )

    collect_logs = await CollectLogsFactory.save_to_db(
        session=agent_session,
        requested_source_keys=["backend", "nginx"],
        unknown_requested_source_keys=["nginx"],
        requested_since="1h",
        warnings=["nginx was not found."],
        retry_tips=["Retry with valid source keys."],
    )
    collected_source = await CollectLogsSourceFactory.save_to_db(
        collect_logs=collect_logs,
        file=source_file_name,
    )
    unavailable_source = await UnavailableCollectLogsSourceFactory.save_to_db(
        collect_logs=collect_logs,
    )

    fetched_snapshot = await CollectLogs.objects.get(id=collect_logs.id)
    fetched_sources = await fetched_snapshot.sources.all()

    assert fetched_snapshot.workspace == LogWorkspace.SESSION
    assert getattr(fetched_snapshot, "session_id") == agent_session.id
    assert fetched_snapshot.project_name == collect_logs.project_name
    assert fetched_snapshot.snapshot_dir == collect_logs.snapshot_dir
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
    assert fetched_sources[0].file.name == source_file_name
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
@pytest.mark.usefixtures("db")
async def test_deleting_mcp_caller_cascades_owned_session_rows() -> None:
    caller = await McpCallerFactory.save_to_db()
    agent_session = await AgentSessionFactory.save_to_db(
        caller=caller,
    )
    agent_call = await AgentCall.objects.create(
        session=agent_session,
        caller=caller,
        event=AgentCallEvent.MCP_CALL_TOOL,
        tool_name="collect_logs",
        success=True,
        project_name="landingpage",
        source_keys=["backend"],
        arguments={"source_keys": ["backend"]},
    )
    collect_logs = await CollectLogsFactory.save_to_db(
        session=agent_session,
    )
    collected_source = await CollectLogsSourceFactory.save_to_db(
        collect_logs=collect_logs,
    )

    deleted_rows = await McpCaller.objects.filter(id=caller.id).delete()

    assert deleted_rows == 1
    assert await McpCaller.objects.filter(id=caller.id).count() == 0
    assert await AgentSession.objects.filter(id=agent_session.id).count() == 0
    assert await AgentCall.objects.filter(id=agent_call.id).count() == 0
    assert await CollectLogs.objects.filter(id=collect_logs.id).count() == 0
    assert await CollectLogsSource.objects.filter(id=collected_source.id).count() == 0


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_agent_session_model_tracks_caller_and_status() -> None:
    """Verify AgentSession owns the lifecycle state for interactive sessions."""

    caller = await McpCallerFactory.save_to_db()
    session = await AgentSessionFactory.save_to_db(
        caller=caller,
    )

    fetched = await AgentSession.objects.get(id=session.id)
    assert fetched.name == session.name
    assert getattr(fetched, "caller_id") == caller.id
    assert fetched.status == AgentSessionStatus.ACTIVE
    assert fetched.closed_at is None


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_log_collection_service_persists_collect_logs_metadata(
    tmp_path: Path,
) -> None:
    """Verify collect_logs orchestration writes artifact and source rows."""

    logs_dir = tmp_path / "collected-logs"
    caller = await McpCallerFactory.save_to_db()
    agent_session = await AgentSessionFactory.save_to_db(
        caller=caller,
    )
    manifest = runtime_test_manifest(load_project_manifest(TEST_MANIFESTS_DIR, "landingpage"))
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
            workspace=LogWorkspace.WORKFLOW,
            session_id=agent_session.name,
            since="2026-04-29T10:59:00Z",
            until=None,
        )
        assert not isinstance(payload, BuildLogsError)
        rows = await CollectLogs.objects.filter(project_name="landingpage")
        assert len(rows) == 1
        collect_logs = rows[0]
        sources = await CollectLogsSource.objects.filter(collect_logs=collect_logs)

        assert collect_logs.workspace == LogWorkspace.WORKFLOW
        assert getattr(collect_logs, "session_id") == agent_session.id
        assert collect_logs.is_latest is True
        assert collect_logs.requested_source_keys == ["app_file", "missing"]
        assert collect_logs.resolved_source_keys == ["app_file"]
        assert collect_logs.unknown_requested_source_keys == ["missing"]
        assert collect_logs.requested_since == "2026-04-29T10:59:00Z"
        assert collect_logs.requested_until is None
        assert collect_logs.snapshot_dir == payload.snapshot_dir

        assert len(sources) == 1
        assert sources[0].source_key == "app_file"
        assert sources[0].status == CollectLogsSourceStatus.COLLECTED
        assert sources[0].file is not None
        assert sources[0].file.name == "workflow/landingpage/latest/app_file.log"
        assert sources[0].file.url == "workflow/landingpage/latest/app_file.log"
        assert sources[0].line_count > 0


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_log_collection_service_persists_session_source_file_path(
    tmp_path: Path,
) -> None:
    """Verify session collect_logs source files keep logs-root-relative DB paths."""

    logs_dir = tmp_path / "collected-logs"
    caller = await McpCallerFactory.save_to_db()
    agent_session = await AgentSessionFactory.save_to_db(
        caller=caller,
    )
    manifest = runtime_test_manifest(load_project_manifest(TEST_MANIFESTS_DIR, "landingpage"))
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
            workspace=LogWorkspace.SESSION,
            session_id=agent_session.name,
            since="2026-04-29T10:59:00Z",
            until=None,
        )
        assert not isinstance(payload, BuildLogsError)
        collect_logs = await CollectLogs.objects.get(project_name="landingpage")
        sources = await CollectLogsSource.objects.filter(collect_logs=collect_logs)

        assert collect_logs.workspace == LogWorkspace.SESSION
        assert getattr(collect_logs, "session_id") == agent_session.id
        assert len(sources) == 1
        assert sources[0].file is not None
        assert sources[0].file.name == f"sessions/{agent_session.name}/landingpage/app_file.log"
        assert sources[0].file.url == f"sessions/{agent_session.name}/landingpage/app_file.log"
