"""Postgres integration tests for database service modules."""

from __future__ import annotations

from uuid import uuid4

import pytest

from database.services.agent_calls import AgentCallService
from database.services.models import AgentCallCreate, AgentCallFilter, AgentCallUpdate
from database.services.project_manifests import ProjectManifestService
from manifests.models import Manifest, SourceDefinition


@pytest.mark.db
@pytest.mark.anyio
async def test_database_services_round_trip_against_real_postgres() -> None:
    agent_calls = AgentCallService()
    project_manifests = ProjectManifestService()
    suffix = uuid4().hex
    project_key = f"integration-{suffix}"
    session_id = uuid4()

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

    stored_manifest = await project_manifests.create_or_update(manifest)
    created_call = await agent_calls.create(
        AgentCallCreate(
            session_id=session_id,
            workspace="workflow",
            event="mcp_call_tool",
            subject="integration-subject",
            client_id="integration-client",
            client_type="agent",
            tool_name="collect_logs",
            duration_ms=25.5,
            project_name=project_key,
            source_keys=["backend"],
            arguments={"tail_lines": 50},
            result_summary={"files": [{"source_key": "backend"}]},
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
    assert fetched_call.result_summary == {"files": [{"source_key": "backend"}]}
    assert [row.id for row in calls_for_session] == [created_call.id]
    assert ended_call.session_ended is True
