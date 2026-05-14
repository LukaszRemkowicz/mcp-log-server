from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp.server.auth import AccessToken

from database.fields import FileReference
from database.models import CollectLogsSource
from tests.conftest import (
    _seed_project_manifests,
    copy_manifest_and_log_fixtures,
    override_settings,
)
from tools.collection import collect_logs
from tools.snapshots import grep_log_snapshot, list_log_snapshot_files, read_log_snapshot_file


@pytest.mark.anyio
async def test_list_read_and_grep_log_snapshot_use_persisted_workflow_snapshot(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        collect_result = await collect_logs(
            project_names=["landingpage"],
            source_keys=["snapshot_text"],
            workspace="workflow",
            since=None,
            until=None,
            access_token=valid_access_token,
        )
        collect_payload = collect_result.structured_content
        assert collect_payload is not None

        list_result = await list_log_snapshot_files(
            project_name="landingpage",
            access_token=valid_access_token,
        )
        list_payload = list_result.structured_content
        assert list_payload is not None

        assert list_payload["action"] == "list_log_snapshot_files"
        assert list_payload["files"][0]["source_key"] == "snapshot_text"

        read_result = await read_log_snapshot_file(
            source_key="snapshot_text",
            project_name="landingpage",
            max_bytes=5,
            access_token=valid_access_token,
        )
        read_payload = read_result.structured_content
        assert read_payload is not None

        assert read_payload["action"] == "read_log_snapshot_file"
        assert read_payload["start_line"] == 1
        assert read_payload["line_count"] == 6
        assert read_payload["content"] == "alpha"
        assert read_payload["truncated"] is True
        assert read_payload["file"]["source_key"] == "snapshot_text"

        grep_result = await grep_log_snapshot(
            grep="match",
            project_name="landingpage",
            source_keys=["snapshot_text"],
            access_token=valid_access_token,
        )
        grep_payload = grep_result.structured_content
        assert grep_payload is not None

    assert grep_payload["action"] == "grep_log_snapshot"
    assert grep_payload["match_offset"] == 0
    assert grep_payload["match_limit"] == 100
    assert grep_payload["match_count"] == 4
    assert grep_payload["returned_match_count"] == 4
    assert grep_payload["truncated"] is False
    assert grep_payload["matched_source_keys"] == ["snapshot_text"]
    assert grep_payload["matches"][0]["line"] == "match one"
    assert grep_payload["matches"][0]["line_truncated"] is False
    assert grep_payload["matches"][1]["line"] == "match two"
    assert grep_payload["matches"][1]["line_truncated"] is False


@pytest.mark.anyio
async def test_read_log_snapshot_file_rejects_tampered_absolute_metadata_path(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("TOP_SECRET\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        collect_result = await collect_logs(
            project_names=["landingpage"],
            source_keys=["snapshot_text"],
            workspace="workflow",
            access_token=valid_access_token,
        )
        collect_payload = collect_result.structured_content
        assert collect_payload is not None
        source_row = await CollectLogsSource.objects.get(source_key="snapshot_text")
        source_row.file = FileReference(outside_file.as_posix())
        await source_row.save(update_fields=["file"])

        read_result = await read_log_snapshot_file(
            source_key="snapshot_text",
            project_name="landingpage",
            access_token=valid_access_token,
        )
    read_payload = read_result.structured_content
    assert read_payload is not None
    assert read_payload["status"] == "error"
    assert read_payload["error_code"] == "invalid_snapshot_file_metadata"


@pytest.mark.anyio
async def test_grep_log_snapshot_rejects_unknown_source_keys(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        await collect_logs(
            project_names=["landingpage"],
            source_keys=["snapshot_text"],
            workspace="workflow",
            access_token=valid_access_token,
        )

        grep_result = await grep_log_snapshot(
            grep="match",
            project_name="landingpage",
            source_keys=["typo"],
            access_token=valid_access_token,
        )
    grep_payload = grep_result.structured_content
    assert grep_payload is not None
    assert grep_payload["status"] == "error"
    assert grep_payload["error_code"] == "snapshot_source_key_not_found"


@pytest.mark.anyio
async def test_grep_log_snapshot_supports_paged_match_windows(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        await collect_logs(
            project_names=["landingpage"],
            source_keys=["snapshot_text"],
            workspace="workflow",
            access_token=valid_access_token,
        )

        grep_result = await grep_log_snapshot(
            grep="match",
            project_name="landingpage",
            source_keys=["snapshot_text"],
            match_offset=1,
            match_limit=2,
            access_token=valid_access_token,
        )
    grep_payload = grep_result.structured_content
    assert grep_payload is not None

    assert grep_payload["match_offset"] == 1
    assert grep_payload["match_limit"] == 2
    assert grep_payload["match_count"] == 4
    assert grep_payload["returned_match_count"] == 2
    assert grep_payload["truncated"] is True
    assert grep_payload["matches"][0]["line"] == "match two"
    assert grep_payload["matches"][1]["line"] == "match three"


@pytest.mark.anyio
async def test_grep_log_snapshot_truncates_large_match_lines(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    long_line = "match " + ("x" * 40)
    fixture_root = copy_manifest_and_log_fixtures(tmp_path)
    log_file = fixture_root / "logs" / "landingpage" / "app_file.log"
    log_file.write_text(f"{long_line}\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"
    await _seed_project_manifests(fixture_root / "manifests")

    with override_settings(LOGS_DIR=logs_dir):
        await collect_logs(
            project_names=["landingpage"],
            source_keys=["app_file"],
            workspace="workflow",
            access_token=valid_access_token,
        )

        grep_result = await grep_log_snapshot(
            grep="match",
            project_name="landingpage",
            source_keys=["app_file"],
            access_token=valid_access_token,
        )
    grep_payload = grep_result.structured_content
    assert grep_payload is not None

    assert grep_payload["match_count"] == 1
    assert grep_payload["returned_match_count"] == 1
    assert grep_payload["matches"][0]["line_truncated"] is False
    assert grep_payload["matches"][0]["line"] == long_line


@pytest.mark.anyio
async def test_grep_log_snapshot_truncates_very_large_match_lines(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    long_line = "match " + ("x" * 4000)
    fixture_root = copy_manifest_and_log_fixtures(tmp_path)
    log_file = fixture_root / "logs" / "landingpage" / "app_file.log"
    log_file.write_text(f"{long_line}\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"
    await _seed_project_manifests(fixture_root / "manifests")

    with override_settings(LOGS_DIR=logs_dir):
        await collect_logs(
            project_names=["landingpage"],
            source_keys=["app_file"],
            workspace="workflow",
            access_token=valid_access_token,
        )

        grep_result = await grep_log_snapshot(
            grep="match",
            project_name="landingpage",
            source_keys=["app_file"],
            access_token=valid_access_token,
        )
    grep_payload = grep_result.structured_content
    assert grep_payload is not None

    assert grep_payload["match_count"] == 1
    assert grep_payload["returned_match_count"] == 1
    assert grep_payload["matches"][0]["line_truncated"] is True
    assert grep_payload["matches"][0]["line"] == long_line.encode("utf-8")[:2000].decode(
        "utf-8",
        errors="ignore",
    )


@pytest.mark.anyio
async def test_read_log_snapshot_file_supports_line_chunks(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        await collect_logs(
            project_names=["landingpage"],
            source_keys=["snapshot_text"],
            workspace="workflow",
            access_token=valid_access_token,
        )

        read_result = await read_log_snapshot_file(
            source_key="snapshot_text",
            project_name="landingpage",
            start_line=2,
            line_count=2,
            max_bytes=100,
            access_token=valid_access_token,
        )
    read_payload = read_result.structured_content
    assert read_payload is not None
    assert read_payload["start_line"] == 2
    assert read_payload["line_count"] == 2
    assert read_payload["content"] == "match one\nmatch two\n"


@pytest.mark.anyio
async def test_grep_log_snapshot_matches_across_multiple_persisted_files(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        collect_result = await collect_logs(
            project_names=["landingpage"],
            source_keys=["app_first", "app_second"],
            workspace="workflow",
            since=None,
            until=None,
            access_token=valid_access_token,
        )
        collect_payload = collect_result.structured_content
        assert collect_payload is not None
        collected_project = collect_payload["projects"][0]

        grep_result = await grep_log_snapshot(
            grep="shared match",
            project_name="landingpage",
            source_keys=["app_first", "app_second"],
            access_token=valid_access_token,
        )
    grep_payload = grep_result.structured_content
    assert grep_payload is not None

    assert collected_project["resolved_source_keys"] == ["app_first", "app_second"]
    assert grep_payload["action"] == "grep_log_snapshot"
    assert grep_payload["grep"] == "shared match"
    assert grep_payload["match_offset"] == 0
    assert grep_payload["match_limit"] == 100
    assert grep_payload["match_count"] == 3
    assert grep_payload["returned_match_count"] == 3
    assert grep_payload["truncated"] is False
    assert grep_payload["matched_source_keys"] == ["app_first", "app_second"]
    assert grep_payload["searched_source_keys"] == ["app_first", "app_second"]
    assert grep_payload["matches"][0]["source_key"] == "app_first"
    assert grep_payload["matches"][0]["line"] == "shared match"
    assert grep_payload["matches"][0]["line_truncated"] is False
    assert grep_payload["matches"][1]["source_key"] == "app_second"
    assert grep_payload["matches"][1]["line"] == "shared match two"
    assert grep_payload["matches"][1]["line_truncated"] is False
    assert grep_payload["matches"][2]["source_key"] == "app_second"
    assert grep_payload["matches"][2]["line"] == "shared match three"
    assert grep_payload["matches"][2]["line_truncated"] is False
