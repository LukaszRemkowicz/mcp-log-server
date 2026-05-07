from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docker.errors import DockerException
from fastmcp.server.auth import AccessToken
from pytest_mock import MockerFixture
from requests import exceptions as requests_exceptions

from conf import settings
from manifests.models import SourceDefinition
from services.log_collection import BuildLogsError, CollectSourceError, LogCollectionService
from services.project_authorization import ProjectAuthorizationError, ProjectAuthorizationService
from services.project_manifest import ProjectManifestService
from tests.conftest import FakeDockerClient, copy_mutable_log_fixture_root, override_settings
from tools.models import SnapshotWorkspace


def build_collect_logs(
    token: AccessToken,
    *,
    requested_project_name: str | None,
    requested_source_keys: list[str] | None,
    workspace: SnapshotWorkspace,
    session_id: str | None = None,
    since: str | None,
    until: str | None,
):
    """Assemble a collection payload directly through the real services for tests."""

    manifest_service = ProjectManifestService()
    project_authorization_service = ProjectAuthorizationService()
    collection_service = LogCollectionService()
    project_name = project_authorization_service.authorize_caller_for_project(
        token,
        requested_project_name,
    )
    if isinstance(project_name, ProjectAuthorizationError):
        raise ValueError(project_name.message)
    manifest_result = manifest_service.get(project_name)
    if manifest_result is None:
        raise ValueError(
            f"Unknown project {project_name!r}. No manifest file was found for that project."
        )
    normalized_since = since or settings.DEFAULT_LOG_WINDOW
    manifest_sources = manifest_service.get_manifest_source_keys(
        manifest_result.manifest,
        requested_source_keys,
    )

    return collection_service.build_logs(
        manifest=manifest_result.manifest,
        sources=manifest_sources.sources,
        missing_source_keys=manifest_sources.missing_source_keys,
        source_keys=manifest_sources.source_keys,
        workspace=workspace,
        session_id=session_id,
        since=normalized_since,
        until=until,
    )


def collect_source(
    definition: SourceDefinition,
    *,
    output_file: Path,
    since: str | None,
    until: str | None,
):
    """Collect one source directly through the deterministic adapter service for tests."""

    return LogCollectionService().collect_source(
        definition,
        output_file=output_file,
        since=since,
        until=until,
    )


def test_build_collect_logs_collects_requested_file_source(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"
    source_file = settings.file_source_root / "landingpage" / "app_file.log"
    expected_content = source_file.read_text(encoding="utf-8")

    with override_settings(LOGS_DIR=logs_dir):
        payload = build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file", "unknown_source"],
            workspace="workflow",
            since=None,
            until=None,
        )

    assert payload["requested_project_name"] == "landingpage"
    assert payload["project_name"] == "landingpage"
    assert payload["workspace"] == "workflow"
    assert payload["requested_since"] == "24h"
    assert payload["requested_until"] is None
    assert payload["resolved_source_keys"] == ["app_file"]
    assert payload["unknown_requested_source_keys"] == ["unknown_source"]
    assert payload["warnings"] == [
        "Some requested source_keys were not found in the configured manifest: unknown_source."
    ]
    assert payload["retry_tips"] == [
        "Retry with only source_keys returned by the manifest-backed project configuration."
    ]
    latest_dir = logs_dir / "workflow" / "landingpage" / "latest"
    archive_dir = logs_dir / "workflow" / "landingpage" / "archive"

    assert payload.snapshot_dir == str(latest_dir)
    assert payload.persisted is True
    assert Path(payload.metadata_file).exists()
    assert Path(payload.metadata_file).name == "workflow_inventory.json"
    assert payload.sources[0].status == "collected"
    output_file = payload.sources[0].output_file
    assert output_file is not None
    assert (logs_dir / output_file).read_text(encoding="utf-8") == expected_content
    assert not (latest_dir / "collected_at.txt").exists()
    assert not (latest_dir / "snapshot_metadata.json").exists()
    assert archive_dir.exists()


def test_build_collect_logs_uses_runtime_default_log_window(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir, DEFAULT_LOG_WINDOW="12h"):
        payload = build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            since=None,
            until=None,
        )

    assert payload["requested_since"] == "12h"


def test_build_collect_logs_archives_previous_latest_snapshot(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    fixture_root = copy_mutable_log_fixture_root(tmp_path)
    log_file = fixture_root / "logs" / "landingpage" / "app_file.log"
    log_file.write_text("first\nsecond\nthird\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"

    with override_settings(
        MANIFEST_PATH=fixture_root / "manifests",
        FILE_SOURCE_ROOT=fixture_root / "logs",
        LOGS_DIR=logs_dir,
    ):
        build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            since=None,
            until=None,
        )

        log_file.write_text("fourth\nfifth\nsixth\n", encoding="utf-8")

        build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            since=None,
            until=None,
        )

    latest_dir = logs_dir / "workflow" / "landingpage" / "latest"
    archive_root = logs_dir / "workflow" / "landingpage" / "archive"
    archived_snapshots = [path for path in archive_root.iterdir() if path.is_dir()]

    assert (latest_dir / "app_file.log").read_text(encoding="utf-8") == "fourth\nfifth\nsixth\n"
    assert archived_snapshots
    assert (archived_snapshots[0] / "app_file.log").read_text(encoding="utf-8") == (
        "first\nsecond\nthird\n"
    )


def test_workflow_inventory_points_to_latest_and_archived_files(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    fixture_root = copy_mutable_log_fixture_root(tmp_path)
    log_file = fixture_root / "logs" / "landingpage" / "app_file.log"
    log_file.write_text("first\nsecond\nthird\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"

    with override_settings(
        MANIFEST_PATH=fixture_root / "manifests",
        FILE_SOURCE_ROOT=fixture_root / "logs",
        LOGS_DIR=logs_dir,
    ):
        build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            since=None,
            until=None,
        )
        log_file.write_text("fourth\nfifth\nsixth\n", encoding="utf-8")
        build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            since=None,
            until=None,
        )

    archive_root = logs_dir / "workflow" / "landingpage" / "archive"
    archived_snapshot = next(path for path in archive_root.iterdir() if path.is_dir())
    workflow_inventory = json.loads(
        (logs_dir / "workflow" / "landingpage" / "workflow_inventory.json").read_text(
            encoding="utf-8"
        )
    )

    assert workflow_inventory["latest"]["files"][0]["output_file"] == (
        "workflow/landingpage/latest/app_file.log"
    )
    assert workflow_inventory["archives"][0]["files"][0]["output_file"] == (
        f"workflow/landingpage/archive/{archived_snapshot.name}/app_file.log"
    )


def test_build_collect_logs_replaces_incomplete_workflow_latest_snapshot(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"
    source_file = settings.file_source_root / "landingpage" / "app_file.log"
    expected_content = source_file.read_text(encoding="utf-8")

    latest_dir = logs_dir / "workflow" / "landingpage" / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / "stale.log").write_text("stale\n", encoding="utf-8")

    with override_settings(LOGS_DIR=logs_dir):
        payload = build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            since=None,
            until=None,
        )

    assert payload.snapshot_dir == str(latest_dir)
    assert not (latest_dir / "stale.log").exists()
    assert (latest_dir / "app_file.log").read_text(encoding="utf-8") == expected_content


def test_session_snapshot_cleanup_uses_configured_retention_window(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    sessions_root = logs_dir / "sessions"
    old_session_root = sessions_root / "session_old"
    old_snapshot = old_session_root / "landingpage"
    old_snapshot.mkdir(parents=True, exist_ok=True)
    old_file = old_snapshot / "backend.log"
    old_file.write_text("old\n", encoding="utf-8")
    old_timestamp = (datetime.now(UTC) - timedelta(minutes=11)).timestamp()
    old_session_root.touch()
    old_snapshot.touch()
    old_file.touch()
    os.utime(old_session_root, (old_timestamp, old_timestamp))
    os.utime(old_file, (old_timestamp, old_timestamp))

    recent_session_root = sessions_root / "session_recent"
    recent_snapshot = recent_session_root / "landingpage"
    recent_snapshot.mkdir(parents=True, exist_ok=True)
    recent_file = recent_snapshot / "backend.log"
    recent_file.write_text("recent\n", encoding="utf-8")

    with override_settings(LOGS_DIR=logs_dir, LOG_SNAPSHOT_RETENTION="10m"):
        payload = build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="session",
            session_id="cleanup-session",
            since=None,
            until=None,
        )

    assert not old_session_root.exists()
    assert recent_session_root.exists()
    assert payload.session_id == "cleanup-session"
    assert (sessions_root / "cleanup-session" / "landingpage").exists()


def test_build_collect_logs_rejects_project_mismatch(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        with pytest.raises(
            ValueError,
            match="Requested project is not allowed by the authenticated access token.",
        ):
            build_collect_logs(
                valid_access_token,
                requested_project_name="other-project",
                requested_source_keys=None,
                workspace="workflow",
                since=None,
                until=None,
            )


def test_build_collect_logs_collects_full_window_without_tail_controls(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        payload = build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            since=None,
            until=None,
        )

    assert payload["warnings"] == []
    assert payload["retry_tips"] == []


def test_build_collect_logs_persists_large_file_without_inline_logs(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    fixture_root = copy_mutable_log_fixture_root(tmp_path)
    log_file = fixture_root / "logs" / "landingpage" / "app_file.log"
    full_content = "x" * 200_001
    log_file.write_text(full_content, encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"

    with override_settings(
        MANIFEST_PATH=fixture_root / "manifests",
        FILE_SOURCE_ROOT=fixture_root / "logs",
        LOGS_DIR=logs_dir,
    ):
        payload = build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            since=None,
            until=None,
        )

    assert payload.sources[0].status == "collected"
    assert payload.sources[0].byte_count == len(full_content.encode("utf-8"))
    assert payload.sources[0].output_file is not None
    assert (logs_dir / payload.sources[0].output_file).read_text(encoding="utf-8") == full_content
    assert payload["snapshot_dir"] == str(logs_dir / "workflow" / "landingpage" / "latest")
    assert payload["warnings"] == []


def test_collect_source_reports_docker_timeout_with_time_window_tip(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    definition = SourceDefinition(
        source_key="backend",
        source_type="docker",
        target="backend-container",
        description="Backend logs.",
        required=True,
        parser_type="plain_text",
        normalization_profile="backend",
        retention_class="short",
        default_noise_profile="noise",
        stream="stdout",
    )
    fake_docker_client.logs_exception = requests_exceptions.Timeout()

    mocker.patch(
        "services.log_collection.docker.from_env",
        return_value=fake_docker_client,
    )

    result = collect_source(
        definition,
        output_file=tmp_path / "backend-timeout.log",
        since=None,
        until=None,
    )

    assert isinstance(result, CollectSourceError)
    assert "Retry with a narrower since/until window" in str(result["error"])
    assert result["retry_tips"] == [
        "Retry with a narrower since/until window to keep docker log output bounded."
    ]


def test_collect_source_uses_docker_sdk_filters(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    definition = SourceDefinition(
        source_key="backend",
        source_type="docker",
        target="backend-container",
        description="Backend logs.",
        required=True,
        parser_type="plain_text",
        normalization_profile="backend",
        retention_class="short",
        default_noise_profile="noise",
        stream="stdout",
    )

    mocker.patch(
        "services.log_collection.docker.from_env",
        return_value=fake_docker_client,
    )

    result = collect_source(
        definition,
        output_file=tmp_path / "backend-filters.log",
        since="30m",
        until="10m",
    )

    assert result["output_file"] == str(tmp_path / "backend-filters.log")
    assert result["line_count"] == 2
    assert result["byte_count"] == 22
    assert (tmp_path / "backend-filters.log").read_text(encoding="utf-8") == (
        "log line 1\nlog line 2\n"
    )
    captured = fake_docker_client.captured_logs_kwargs
    assert captured["timestamps"] is True
    assert captured["stdout"] is True
    assert captured["stderr"] is True
    captured_since = captured["since"]
    captured_until = captured["until"]
    assert isinstance(captured_since, datetime)
    assert isinstance(captured_until, datetime)
    assert captured_since.tzinfo == UTC
    assert captured_until.tzinfo == UTC


def test_collect_source_streams_persisted_docker_logs_without_following(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    definition = SourceDefinition(
        source_key="backend",
        source_type="docker",
        target="backend-container",
        description="Backend logs.",
        required=True,
        parser_type="plain_text",
        normalization_profile="backend",
        retention_class="short",
        default_noise_profile="noise",
        stream="stdout",
    )

    mocker.patch(
        "services.log_collection.docker.from_env",
        return_value=fake_docker_client,
    )

    output_file = tmp_path / "backend.log"
    result = LogCollectionService().collect_source(
        definition,
        output_file=output_file,
        since="30m",
        until="10m",
    )

    assert result["output_file"] == str(output_file)
    assert result["line_count"] == 2
    assert result["byte_count"] == 22
    assert output_file.read_text(encoding="utf-8") == "log line 1\nlog line 2\n"
    captured = fake_docker_client.captured_logs_kwargs
    assert captured["stream"] is True
    assert captured["follow"] is False


def test_collect_source_streams_persisted_file_logs_to_output_file(tmp_path) -> None:
    source_file = tmp_path / "source.log"
    source_file.write_text("log line 1\nlog line 2\n", encoding="utf-8")
    definition = SourceDefinition(
        source_key="app_file",
        source_type="file",
        target="source.log",
        description="Application file logs.",
        required=True,
        parser_type="plain_text",
        normalization_profile="app",
        retention_class="short",
        default_noise_profile="noise",
        stream=None,
    )

    output_file = tmp_path / "persisted.log"
    with override_settings(MANIFEST_PATH=tmp_path / "manifests", FILE_SOURCE_ROOT=tmp_path):
        result = LogCollectionService().collect_source(
            definition,
            output_file=output_file,
            since=None,
            until=None,
        )

    assert result["output_file"] == str(output_file)
    assert result["line_count"] == 2
    assert result["byte_count"] == output_file.stat().st_size
    assert output_file.read_text(encoding="utf-8") == "log line 1\nlog line 2\n"


def test_collect_source_rejects_relative_symlink_escape(tmp_path: Path) -> None:
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "source.log"
    outside_file.write_text("escaped\n", encoding="utf-8")
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir()
    file_source_root = tmp_path / "logs"
    file_source_root.mkdir()
    (file_source_root / "escape").symlink_to(outside_dir, target_is_directory=True)
    definition = SourceDefinition(
        source_key="app_file",
        source_type="file",
        target="escape/source.log",
        description="Application file logs.",
        required=True,
        parser_type="plain_text",
        normalization_profile="app",
        retention_class="short",
        default_noise_profile="noise",
        stream=None,
    )

    with override_settings(MANIFEST_PATH=manifest_dir, FILE_SOURCE_ROOT=file_source_root):
        result = LogCollectionService().collect_source(
            definition,
            output_file=tmp_path / "persisted.log",
            since=None,
            until=None,
        )

    assert isinstance(result, CollectSourceError)
    assert "resolves outside" in result.error


def test_collect_source_reports_docker_api_unavailable(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    definition = SourceDefinition(
        source_key="backend",
        source_type="docker",
        target="backend-container",
        description="Backend logs.",
        required=True,
        parser_type="plain_text",
        normalization_profile="backend",
        retention_class="short",
        default_noise_profile="noise",
        stream="stdout",
    )

    def fake_from_env(timeout: int) -> object:
        raise DockerException("socket unavailable")

    mocker.patch("services.log_collection.docker.from_env", side_effect=fake_from_env)

    result = collect_source(
        definition,
        output_file=tmp_path / "backend-unavailable.log",
        since=None,
        until=None,
    )

    assert isinstance(result, CollectSourceError)
    assert result["error"] == "Docker Engine API is not available in the current runtime."
    assert result["retry_tips"] == [
        "Retry in a runtime where the Docker socket is mounted and reachable."
    ]


def test_build_collect_logs_requires_agent_chosen_session_id(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        payload = build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="session",
            session_id=None,
            since=None,
            until=None,
        )

    assert isinstance(payload, BuildLogsError)
    assert payload.error_code == "missing_session_id"
    assert "session_id is required" in payload.message


def test_build_collect_logs_reuses_agent_chosen_session_id(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        payload = build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="session",
            session_id="agent-session-1",
            since=None,
            until=None,
        )

    assert payload.workspace == "session"
    assert payload.session_id == "agent-session-1"
    snapshot_dir = logs_dir / "sessions" / "agent-session-1" / "landingpage"
    assert payload.snapshot_dir == str(snapshot_dir)
    assert snapshot_dir.exists()
    source_file = settings.file_source_root / "landingpage" / "app_file.log"
    assert (snapshot_dir / "app_file.log").read_text(encoding="utf-8") == source_file.read_text(
        encoding="utf-8"
    )
