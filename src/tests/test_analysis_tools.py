from __future__ import annotations

from pathlib import Path

import pytest
from anyio import to_thread
from fastmcp.server.auth import AccessToken

from services.log_filtering import CreateFilteredViewError, LogFilteringService
from tests.conftest import override_settings
from tools import collection as collection_tools
from tools.analysis import (
    build_incident_bundle,
    create_filtered_view,
    group_errors,
    suggest_followup_window,
)
from tools.models import LogSnapshotFilePayload, LogSnapshotMetadata


async def _run_sync_tool(func, **kwargs):
    """Run a sync decorated tool from an async DB-backed test."""

    return await to_thread.run_sync(lambda: func(**kwargs))


def test_log_filtering_service_returns_error_for_unknown_source_key() -> None:
    """Verify filtered-view service returns a typed error for missing sources."""

    service = LogFilteringService()
    metadata = LogSnapshotMetadata(
        project_name="landingpage",
        workspace="workflow",
        session_id=None,
        files=[
            LogSnapshotFilePayload(
                source_key="backend",
                source_type="file",
                description="Backend logs.",
                target="/tmp/backend.log",
                stream=None,
                file_name="backend.log",
                output_file="workflow/landingpage/latest/backend.log",
                byte_count=0,
                line_count=0,
            )
        ],
        collected_at="2026-05-05T10:00:00Z",
    )

    result = service.create_filtered_view(
        metadata,
        source_contexts={},
        source_keys=["missing_source"],
        max_lines=10,
        requested_project_name="landingpage",
        project_name="landingpage",
        next_step_tips=[],
    )

    assert isinstance(result, CreateFilteredViewError)
    assert result.error_code == "snapshot_source_key_not_found"
    assert result.message == "Requested log snapshot source_keys were not found: missing_source"


@pytest.mark.db
@pytest.mark.anyio
async def test_group_errors_groups_repeated_failures(
    tmp_path: Path,
    valid_access_token: AccessToken,
    patch_file_project_manifest_service,
) -> None:
    """Verify grouped errors summarize repeated backend and nginx failures."""

    logs_dir = tmp_path / "collected-logs"
    with override_settings(LOGS_DIR=logs_dir):
        await collection_tools.collect_logs(
            project_names=["landingpage"],
            source_keys=["backend", "nginx"],
            workspace="workflow",
            access_token=valid_access_token,
        )
        result = await _run_sync_tool(
            group_errors,
            project_name="landingpage",
            source_keys=["backend", "nginx"],
            access_token=valid_access_token,
        )
    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "group_errors"
    assert payload["searched_source_keys"] == ["backend", "nginx"]
    assert payload["analysis_cautions"] == [
        "Use grouped findings for triage, not as the final incident conclusion.",
        (
            "Confirm timing, clustering, and severity with grep_log_snapshot(...) "
            "or read_log_snapshot_file(...)."
        ),
        (
            "Use the original collection window to judge whether a pattern is "
            "bursty, continuous, or isolated."
        ),
    ]
    assert payload["grouped_error_count"] == 4
    assert payload["matching_line_count"] == 6
    assert payload["summary"].startswith("Found 6 error-like lines in 4 groups.")
    assert payload["truncated"] is False
    assert payload["groups"][0]["category"] == "application_error"
    assert payload["groups"][0]["count"] == 2
    assert payload["groups"][0]["source_keys"] == ["backend"]
    assert payload["snapshot_dir"] == "workflow/landingpage/latest"
    assert (
        payload["groups"][0]["first_seen"]["output_file"]
        == "workflow/landingpage/latest/backend.log"
    )
    assert payload["groups"][0]["message_summary"] == "Database connection failed"
    assert payload["groups"][0]["first_timestamp"] == "2026-04-29T10:00:00Z"
    assert payload["groups"][0]["last_timestamp"] == "2026-04-29T10:05:00Z"
    http_4xx_group = next(item for item in payload["groups"] if item["category"] == "http_4xx")
    assert http_4xx_group["request_paths"] == ["/admin"]
    assert http_4xx_group["first_timestamp"] == "29/Apr/2026:10:10:00 +0000"
    assert http_4xx_group["last_timestamp"] == "29/Apr/2026:10:11:00 +0000"


@pytest.mark.db
@pytest.mark.anyio
async def test_group_errors_summarizes_docker_prefixed_json(
    tmp_path: Path,
    valid_access_token: AccessToken,
    patch_file_project_manifest_service,
) -> None:
    """Verify docker-prefixed JSON lines are parsed and grouped by message."""

    logs_dir = tmp_path / "collected-logs"
    with override_settings(LOGS_DIR=logs_dir):
        await collection_tools.collect_logs(
            project_names=["landingpage"],
            source_keys=["traefik"],
            workspace="workflow",
            access_token=valid_access_token,
        )
        result = await _run_sync_tool(
            group_errors,
            project_name="landingpage",
            source_keys=["traefik"],
            access_token=valid_access_token,
        )
    payload = result.structured_content
    assert payload is not None

    assert payload["grouped_error_count"] == 1
    assert payload["matching_line_count"] == 2
    assert payload["summary"] == (
        "Found 2 error-like lines in 1 group. Top results: "
        "2x medium warning_signal in traefik: Failed to inspect container <id>"
    )
    assert payload["groups"][0]["category"] == "warning_signal"
    assert payload["groups"][0]["count"] == 2
    assert payload["groups"][0]["levels"] == ["WARN"]
    assert payload["groups"][0]["message_summary"] == "Failed to inspect container <id>"
    assert payload["groups"][0]["first_timestamp"] == "2026-04-30T19:12:38Z"
    assert payload["groups"][0]["last_timestamp"] == "2026-04-30T19:12:39Z"


def test_group_errors_rejects_invalid_max_groups(
    valid_access_token: AccessToken,
) -> None:
    """Verify group_errors rejects invalid pagination limits before loading logs."""

    result = group_errors(
        project_name="landingpage",
        max_groups=0,
        access_token=valid_access_token,
    )
    payload = result.structured_content
    assert payload is not None

    assert payload["status"] == "error"
    assert payload["error_code"] == "invalid_group_window"
    assert payload["message"] == "max_groups must be between 1 and 200."


def test_group_errors_missing_snapshot_tells_agent_to_collect_first(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    """Verify group_errors explains how to recover when no snapshot exists."""

    logs_dir = tmp_path / "collected-logs"
    with override_settings(LOGS_DIR=logs_dir):
        result = group_errors(
            project_name="landingpage",
            access_token=valid_access_token,
        )
    payload = result.structured_content
    assert payload is not None

    assert payload["status"] == "error"
    assert payload["error_code"] == "snapshot_not_found"
    assert payload["retry_tips"] == [
        "Run collect_logs first to create a persisted snapshot for this project and window.",
        (
            "Retry with session_id plus project_name for session artifacts, "
            "or with project_name plus an optional archive_name for workflow artifacts."
        ),
    ]


@pytest.mark.db
@pytest.mark.anyio
async def test_build_incident_bundle_returns_grouped_summary(
    tmp_path: Path,
    valid_access_token: AccessToken,
    patch_file_project_manifest_service,
) -> None:
    """Verify incident bundles include grouped counts, severities, and next steps."""

    logs_dir = tmp_path / "collected-logs"
    with override_settings(LOGS_DIR=logs_dir):
        await collection_tools.collect_logs(
            project_names=["landingpage"],
            source_keys=["app_file"],
            workspace="workflow",
            access_token=valid_access_token,
        )
        result = await _run_sync_tool(
            build_incident_bundle,
            project_name="landingpage",
            source_keys=["app_file"],
            access_token=valid_access_token,
        )
    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "build_incident_bundle"
    assert payload["analysis_cautions"] == [
        "Use grouped findings for triage, not as the final incident conclusion.",
        (
            "Confirm timing, clustering, and severity with grep_log_snapshot(...) "
            "or read_log_snapshot_file(...)."
        ),
        (
            "Use the original collection window to judge whether a pattern is "
            "bursty, continuous, or isolated."
        ),
    ]
    assert payload["grouped_error_count"] == 2
    assert payload["matching_line_count"] == 2
    assert payload["high_severity_group_count"] == 2
    assert payload["medium_severity_group_count"] == 0
    assert payload["source_summaries"][0]["source_key"] == "app_file"
    assert payload["snapshot_dir"] == "workflow/landingpage/latest"
    assert payload["source_summaries"][0]["grouped_error_count"] == 2
    assert payload["source_summaries"][0]["first_timestamp"] == "2026-04-29T11:00:00Z"
    assert payload["source_summaries"][0]["last_timestamp"] == "2026-04-29T11:02:00Z"
    assert payload["top_groups"][0]["first_seen"]["source_key"] == "app_file"
    assert (
        payload["top_groups"][0]["first_seen"]["output_file"]
        == "workflow/landingpage/latest/app_file.log"
    )
    assert payload["top_groups"][0]["first_timestamp"] == "2026-04-29T11:00:00Z"
    assert payload["suggested_next_steps"]


def test_build_incident_bundle_rejects_invalid_max_groups(
    valid_access_token: AccessToken,
) -> None:
    """Verify incident bundles reject invalid group limits consistently."""

    result = build_incident_bundle(
        project_name="landingpage",
        max_groups=201,
        access_token=valid_access_token,
    )
    payload = result.structured_content
    assert payload is not None

    assert payload["status"] == "error"
    assert payload["error_code"] == "invalid_group_window"
    assert payload["message"] == "max_groups must be between 1 and 200."


@pytest.mark.db
@pytest.mark.anyio
async def test_create_filtered_view_removes_manifest_profile_noise(
    tmp_path: Path,
    valid_access_token: AccessToken,
    patch_file_project_manifest_service,
) -> None:
    """Verify filtered views remove manifest-described noise but keep errors."""

    logs_dir = tmp_path / "collected-logs"
    with override_settings(LOGS_DIR=logs_dir):
        await collection_tools.collect_logs(
            project_names=["landingpage"],
            source_keys=["backend", "nginx"],
            workspace="workflow",
            access_token=valid_access_token,
        )
        result = await create_filtered_view(
            project_name="landingpage",
            access_token=valid_access_token,
        )
    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "create_filtered_view"
    assert payload["searched_source_keys"] == ["backend", "nginx"]
    assert payload["total_line_count"] == 10
    assert payload["kept_line_count"] == 7
    assert payload["excluded_line_count"] == 3
    assert payload["returned_line_count"] == 7
    assert payload["truncated"] is False
    assert [item["source_key"] for item in payload["cleaned_lines"]] == [
        "backend",
        "backend",
        "backend",
        "nginx",
        "nginx",
        "nginx",
        "nginx",
    ]
    assert any("Database timeout" in item["line"] for item in payload["cleaned_lines"])
    assert any("/assets/legacy.js" in item["line"] for item in payload["cleaned_lines"])
    assert any("/api/orders" in item["line"] for item in payload["cleaned_lines"])
    assert "excluded_samples" not in payload
    source_summaries = {item["source_key"]: item for item in payload["source_summaries"]}
    assert source_summaries["backend"]["kept_line_count"] == 3
    assert source_summaries["backend"]["excluded_line_count"] == 1
    assert source_summaries["nginx"]["kept_line_count"] == 4
    assert source_summaries["nginx"]["excluded_line_count"] == 2
    assert source_summaries["nginx"]["top_exclusion_reasons"] == ["successful_static_asset_request"]


@pytest.mark.db
@pytest.mark.anyio
async def test_create_filtered_view_reports_unknown_source_key(
    tmp_path: Path,
    valid_access_token: AccessToken,
    patch_file_project_manifest_service,
) -> None:
    """Verify filtered views report unknown requested sources as tool errors."""

    logs_dir = tmp_path / "collected-logs"
    with override_settings(LOGS_DIR=logs_dir):
        await collection_tools.collect_logs(
            project_names=["landingpage"],
            source_keys=["app_file"],
            workspace="workflow",
            access_token=valid_access_token,
        )
        result = await create_filtered_view(
            project_name="landingpage",
            source_keys=["missing_source"],
            access_token=valid_access_token,
        )
    payload = result.structured_content
    assert payload is not None

    assert payload["status"] == "error"
    assert payload["error_code"] == "snapshot_source_key_not_found"
    assert "missing_source" in payload["message"]


def test_suggest_followup_window_returns_collect_logs_range() -> None:
    """Verify follow-up window suggestions produce collect_logs-ready bounds."""

    result = suggest_followup_window(
        first_timestamp="2026-04-29T10:00:00Z",
        last_timestamp="2026-04-29T10:05:00Z",
        padding_minutes=5,
    )
    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "suggest_followup_window"
    assert payload["suggested_since"] == "2026-04-29T09:55:00Z"
    assert payload["suggested_until"] == "2026-04-29T10:10:00Z"
    assert payload["ready_for_collect_logs"] is True
    assert payload["example_collect_logs_arguments"] == {
        "since": "2026-04-29T09:55:00Z",
        "until": "2026-04-29T10:10:00Z",
    }
