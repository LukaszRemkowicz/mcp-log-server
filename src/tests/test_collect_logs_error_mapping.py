from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp.server.auth import AccessToken

from conf import settings
from tests.conftest import override_settings
from tools.errors import (
    build_collect_logs_error_details,
    build_collect_logs_error_result,
    build_collect_logs_error_retry_tips,
    classify_collect_logs_error,
    render_collect_logs_error_message,
)


@pytest.mark.parametrize(
    ("message", "expected_error_code"),
    [
        (
            "Authenticated access token must include allowed_projects, or projects_access='all'.",
            "missing_project_access_claim",
        ),
        (
            "Requested project is not allowed by the authenticated access token.",
            "project_access_mismatch",
        ),
        (
            "Some project_names must not contain empty values.",
            "invalid_project_names",
        ),
        (
            "Unknown project 'other'. No manifest file was found for that project.",
            "unknown_project",
        ),
        (
            "Invalid docker time filter: thirty-minutes",
            "invalid_docker_time_filter",
        ),
        (
            "session_id is required when workspace='session'.",
            "missing_session_id",
        ),
        (
            "validation error for LogSnapshotMetadata",
            "invalid_snapshot_metadata",
        ),
    ],
)
def test_classify_collect_logs_error_returns_expected_codes(
    message: str,
    expected_error_code: str,
) -> None:
    assert classify_collect_logs_error(message) == expected_error_code


def test_build_collect_logs_error_details_returns_project_access_context(
    valid_access_token: AccessToken,
) -> None:
    details = build_collect_logs_error_details(
        "project_access_mismatch",
        settings=settings,
        access_token=valid_access_token,
        project_names=["landingpage"],
        workspace="workflow",
        session_id=None,
    )

    assert details == {
        "requested_project_names": ["landingpage"],
        "allowed_projects": ["landingpage"],
    }


def test_build_collect_logs_error_details_returns_manifest_context(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    manifest_path = tmp_path / "landingpage.json"
    manifest_path.write_text("{}", encoding="utf-8")

    with override_settings(MANIFEST_PATH=manifest_path.parent) as effective_settings:
        details = build_collect_logs_error_details(
            "unknown_project",
            settings=effective_settings,
            access_token=valid_access_token,
            project_names=["other-project"],
            workspace="workflow",
            session_id=None,
        )

    assert details == {
        "requested_project_names": ["other-project"],
        "manifests_dir": str(effective_settings.manifests_dir),
    }


def test_build_collect_logs_error_details_returns_session_context(
    valid_access_token: AccessToken,
) -> None:
    details = build_collect_logs_error_details(
        "missing_session_id",
        settings=settings,
        access_token=valid_access_token,
        project_names=["landingpage"],
        workspace="session",
        session_id=None,
    )

    assert details == {
        "workspace": "session",
        "session_id": None,
    }


def test_render_collect_logs_error_message_sanitizes_snapshot_metadata_errors() -> None:
    message = "validation error for LogSnapshotMetadata"

    rendered = render_collect_logs_error_message("invalid_snapshot_metadata", message)

    assert "Persisted workflow snapshot metadata is incompatible" in rendered


def test_build_collect_logs_error_result_returns_normalized_tool_error(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    manifest_path = tmp_path / "landingpage.json"
    manifest_path.write_text("{}", encoding="utf-8")

    with override_settings(MANIFEST_PATH=manifest_path.parent) as effective_settings:
        result = build_collect_logs_error_result(
            "Unknown project 'other'. No manifest file was found for that project.",
            settings=effective_settings,
            access_token=valid_access_token,
            project_names=["other"],
            workspace="workflow",
            session_id=None,
        )
    mcp_result: Any = result.to_mcp_result()

    assert mcp_result.isError is True
    assert mcp_result.structuredContent["error_code"] == "unknown_project"
    assert mcp_result.structuredContent["details"] == {
        "requested_project_names": ["other"],
        "manifests_dir": str(effective_settings.manifests_dir),
    }
    assert mcp_result.structuredContent["retry_tips"] == build_collect_logs_error_retry_tips(
        "unknown_project"
    )
