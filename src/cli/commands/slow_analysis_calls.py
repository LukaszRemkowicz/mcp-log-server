"""Slow snapshot-analysis call review implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import typer

from database.models import AgentCall, CollectLogs
from database.types import AgentCallEvent
from decorators import async_, db

ANALYSIS_TOOL_NAMES = (
    "create_filtered_view",
    "group_errors",
    "build_incident_bundle",
    "inspect_proxy_activity",
)


@dataclass(frozen=True, slots=True)
class SlowAnalysisCallRow:
    """One operator-facing slow analysis call row."""

    created_at: datetime
    session_id: str
    workspace: str
    tool_name: str
    duration_seconds: float
    success: bool
    error_code: str | None
    project_name: str | None
    source_keys: list[str]
    arguments: dict[str, Any]
    source_size_available: bool
    total_source_line_count: int | None
    source_line_counts: dict[str, int]


async def collect_slow_analysis_call_rows(
    *,
    min_duration: float = 1.0,
    limit: int = 20,
    tool_name: str | None = None,
    project_name: str | None = None,
    success: bool | None = None,
) -> list[SlowAnalysisCallRow]:
    """Return slow snapshot-analysis calls with best-effort source-size context."""

    filters: dict[str, object] = {
        "event": AgentCallEvent.MCP_CALL_TOOL,
        "tool_name__in": ANALYSIS_TOOL_NAMES,
        "duration_seconds__gte": min_duration,
    }
    if tool_name is not None:
        filters["tool_name"] = tool_name
    if project_name is not None:
        filters["project_name"] = project_name
    if success is not None:
        filters["success"] = success

    calls = (
        await AgentCall.objects.filter(**filters)
        .prefetch_related("session", "caller")
        .order_by("-duration_seconds", "-created_at", "id")
        .limit(limit)
    )
    rows: list[SlowAnalysisCallRow] = []
    for call in calls:
        source_line_counts = await _load_source_line_counts(call)
        source_size_available = source_line_counts is not None
        rows.append(
            SlowAnalysisCallRow(
                created_at=call.created_at,
                session_id=call.session.name,
                workspace=call.caller.workspace.value,
                tool_name=call.tool_name or "",
                duration_seconds=call.duration_seconds or 0.0,
                success=call.success,
                error_code=call.error_code,
                project_name=call.project_name,
                source_keys=call.source_keys or [],
                arguments=call.arguments or {},
                source_size_available=source_size_available,
                total_source_line_count=(
                    sum(source_line_counts.values()) if source_line_counts is not None else None
                ),
                source_line_counts=source_line_counts or {},
            )
        )
    return rows


async def _load_source_line_counts(call: AgentCall) -> dict[str, int] | None:
    """Return matching collect_logs source line counts, when metadata can be matched."""

    arguments = call.arguments or {}
    collect_logs_query = CollectLogs.objects.filter(
        session=call.session,
        workspace=call.caller.workspace,
        project_name=call.project_name,
        created_at__lte=call.created_at,
    )
    archive_name = arguments.get("archive_name")
    if isinstance(archive_name, str):
        collect_logs_query = collect_logs_query.filter(archive_name=archive_name)

    collect_logs = (
        await collect_logs_query.prefetch_related("sources").order_by("-created_at", "-id").first()
    )
    if collect_logs is None:
        return None

    selected_source_keys = set(call.source_keys or collect_logs.resolved_source_keys)
    sources = await collect_logs.sources.all()
    return {
        source.source_key: source.line_count
        for source in sources
        if not selected_source_keys or source.source_key in selected_source_keys
    }


def _format_source_line_counts(row: SlowAnalysisCallRow) -> str:
    if not row.source_size_available:
        return "unavailable"
    if not row.source_line_counts:
        return "none"
    parts = [
        f"{source_key}={count}" for source_key, count in sorted(row.source_line_counts.items())
    ]
    return f"total={row.total_source_line_count} ({', '.join(parts)})"


def _echo_slow_analysis_call_rows(rows: list[SlowAnalysisCallRow]) -> None:
    if not rows:
        typer.echo("No slow snapshot-analysis calls found.")
        return

    for row in rows:
        status = "ok" if row.success else f"error:{row.error_code or 'unknown'}"
        source_keys = ",".join(row.source_keys) if row.source_keys else "all/resolved"
        typer.echo(
            " | ".join(
                (
                    row.created_at.isoformat(),
                    f"session={row.session_id}",
                    f"workspace={row.workspace}",
                    f"tool={row.tool_name}",
                    f"duration={row.duration_seconds:.3f}s",
                    f"status={status}",
                    f"project={row.project_name or 'unknown'}",
                    f"sources={source_keys}",
                    f"lines={_format_source_line_counts(row)}",
                    f"args={row.arguments}",
                )
            )
        )


@async_
@db
async def run_slow_analysis_calls(
    min_duration: float = 1.0,
    limit: int = 20,
    tool_name: str | None = None,
    project_name: str | None = None,
    success: bool | None = None,
) -> None:
    """Review slow snapshot-analysis MCP calls."""

    _echo_slow_analysis_call_rows(
        await collect_slow_analysis_call_rows(
            min_duration=min_duration,
            limit=limit,
            tool_name=tool_name,
            project_name=project_name,
            success=success,
        )
    )
