from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cli.commands.slow_analysis_calls import collect_slow_analysis_call_rows
from core.types import LogWorkspace
from database.models import AgentCall
from database.types import AgentCallEvent
from tests.factories import (
    AgentSessionFactory,
    CollectLogsFactory,
    CollectLogsSourceFactory,
    McpCallerFactory,
)


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_collect_slow_analysis_call_rows_attaches_source_line_counts() -> None:
    suffix = uuid4().hex
    caller = await McpCallerFactory.save_to_db(
        client_id=f"phase16e-workflow-agent-{suffix}",
        client_type="workflow_agent",
        workspace=LogWorkspace.WORKFLOW,
    )
    session = await AgentSessionFactory.save_to_db(
        name=f"slow-analysis-{suffix[:8]}",
        caller=caller,
    )
    collect_logs = await CollectLogsFactory.save_to_db(
        session=session,
        workspace=LogWorkspace.WORKFLOW,
        project_name="landingpage",
        requested_source_keys=["backend", "nginx"],
        resolved_source_keys=["backend", "nginx"],
        collected_at=datetime(2026, 6, 12, 9, 30, tzinfo=UTC),
    )
    await CollectLogsSourceFactory.save_to_db(
        collect_logs=collect_logs,
        source_key="backend",
        line_count=120,
    )
    await CollectLogsSourceFactory.save_to_db(
        collect_logs=collect_logs,
        source_key="nginx",
        line_count=80,
    )
    await AgentCall.objects.create(
        session=session,
        caller=caller,
        event=AgentCallEvent.MCP_CALL_TOOL,
        tool_name="inspect_proxy_activity",
        duration_seconds=4.25,
        success=True,
        project_name="landingpage",
        source_keys=["nginx"],
        arguments={"project_name": "landingpage", "session_id": session.name},
    )
    await AgentCall.objects.create(
        session=session,
        caller=caller,
        event=AgentCallEvent.MCP_CALL_TOOL,
        tool_name="collect_logs",
        duration_seconds=99.0,
        success=True,
        project_name="landingpage",
    )

    rows = await collect_slow_analysis_call_rows(min_duration=1.0, limit=10)

    assert len(rows) == 1
    row = rows[0]
    assert row.tool_name == "inspect_proxy_activity"
    assert row.session_id == session.name
    assert row.workspace == "workflow"
    assert row.project_name == "landingpage"
    assert row.source_keys == ["nginx"]
    assert row.duration_seconds == 4.25
    assert row.source_size_available is True
    assert row.total_source_line_count == 80
    assert row.source_line_counts == {"nginx": 80}
