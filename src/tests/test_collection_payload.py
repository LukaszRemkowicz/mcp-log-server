from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastmcp.server.auth import AccessToken

from conf import settings
from core.types import LogWorkspace
from exception import InvalidTimeFilterError
from manifests.loader import list_project_manifests, load_project_manifest
from manifests.models import SourceDefinition
from services.docker_log_gateway import DockerLogGatewayError, ResolvedDockerContainer
from services.log_collection import DockerTimeFilters, LogCollectionService, SourceCollectionResult
from services.project_authorization import ProjectAuthorizationError, ProjectAuthorizationService
from services.project_manifest import ProjectManifestService
from tests.conftest import (
    TEST_FILE_SOURCE_ROOT,
    TEST_MANIFESTS_DIR,
    copy_manifest_and_log_fixtures,
    override_settings,
    runtime_test_manifest,
)
from tests.factories import AgentSessionFactory
from tools.models import SnapshotWorkspace

SESSION_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}-[a-f0-9]{4}$")
SESSION_ID = "gentle-river-finds-a8f2"
SECOND_SESSION_ID = "quiet-field-opens-b1c2"


class FakeDockerLogGateway:
    def __init__(self) -> None:
        self.resolved_by_name: dict[str, ResolvedDockerContainer | None] = {}
        self.resolved_by_project_service: dict[tuple[str, str], ResolvedDockerContainer | None] = {}
        self.name_calls: list[str] = []
        self.project_service_calls: list[tuple[str, str]] = []
        self.stream_calls: list[tuple[str, dict[str, int | str | datetime]]] = []
        self.stream_exception: DockerLogGatewayError | None = None
        self.resolve_exception: DockerLogGatewayError | None = None

    def resolve_container_by_name(self, container_name: str) -> ResolvedDockerContainer | None:
        if self.resolve_exception is not None:
            raise self.resolve_exception
        self.name_calls.append(container_name)
        return self.resolved_by_name.get(container_name)

    def resolve_container_by_project_service(
        self,
        *,
        project_name: str,
        service_name: str,
    ) -> ResolvedDockerContainer | None:
        if self.resolve_exception is not None:
            raise self.resolve_exception
        self.project_service_calls.append((project_name, service_name))
        return self.resolved_by_project_service.get((project_name, service_name))

    def stream_logs(
        self,
        *,
        container_name: str,
        logs_kwargs: dict[str, int | str | datetime],
    ):
        if self.stream_exception is not None:
            raise self.stream_exception
        self.stream_calls.append((container_name, dict(logs_kwargs)))
        yield b"log line 1\n"
        yield b"log line 2\n"


async def build_collect_logs(
    token: AccessToken,
    *,
    requested_project_name: str | None,
    requested_source_keys: list[str] | None,
    workspace: SnapshotWorkspace,
    manifests_dir: Path = TEST_MANIFESTS_DIR,
    session_id: str | None = None,
    since: str | None,
    until: str | None,
):
    """Assemble a collection payload directly through the real services for tests."""

    project_authorization_service = ProjectAuthorizationService()
    collection_service = LogCollectionService()
    if token.claims.get("projects_access") == "all":
        allowed_projects = {
            manifest.project_key for manifest in list_project_manifests(manifests_dir)
        }
    else:
        allowed_projects = {
            str(project_name).strip()
            for project_name in token.claims.get("allowed_projects", [])
            if str(project_name).strip()
        }
    project_name = project_authorization_service.authorize_project(
        allowed_projects=allowed_projects,
        requested_project_name=requested_project_name,
    )
    if isinstance(project_name, ProjectAuthorizationError):
        raise ValueError(project_name.message)
    try:
        manifest = runtime_test_manifest(load_project_manifest(manifests_dir, project_name))
    except FileNotFoundError:
        raise ValueError(
            f"Unknown project {project_name!r}. No manifest file was found for that project."
        ) from None
    normalized_since = since or settings.DEFAULT_LOG_WINDOW
    manifest_sources = ProjectManifestService.get_manifest_source_keys(
        manifest,
        requested_source_keys,
    )
    if session_id is None:
        agent_session = await AgentSessionFactory.save_to_db()
    else:
        agent_session = await AgentSessionFactory.save_to_db(name=session_id)

    return await collection_service.build_logs(
        manifest=manifest,
        sources=manifest_sources.sources,
        missing_source_keys=manifest_sources.missing_source_keys,
        source_keys=manifest_sources.source_keys,
        workspace=workspace,
        session_id=agent_session.name,
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

    service = LogCollectionService()
    return service.collect_source(
        definition,
        output_file=output_file,
        time_filters=service.validate_and_normalize_time_filters(
            sources=[definition],
            since=since,
            until=until,
        ),
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_build_collect_logs_collects_requested_file_source(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"
    source_file = TEST_FILE_SOURCE_ROOT / "landingpage" / "app_file.log"
    expected_content = source_file.read_text(encoding="utf-8")

    with override_settings(LOGS_DIR=logs_dir):
        payload = await build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file", "unknown_source"],
            workspace=LogWorkspace.WORKFLOW,
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
    assert "persisted" not in payload.model_dump()
    assert payload.sources[0].status == "collected"
    output_file = payload.sources[0].output_file
    assert output_file is not None
    assert (logs_dir / output_file).read_text(encoding="utf-8") == expected_content
    assert not (latest_dir / "collected_at.txt").exists()
    assert not (latest_dir / "snapshot_metadata.json").exists()
    assert not (logs_dir / "workflow" / "landingpage" / "workflow_inventory.json").exists()
    assert archive_dir.exists()


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_build_collect_logs_uses_runtime_default_log_window(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir, DEFAULT_LOG_WINDOW="12h"):
        payload = await build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace=LogWorkspace.WORKFLOW,
            since=None,
            until=None,
        )

    assert payload["requested_since"] == "12h"


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_build_collect_logs_archives_previous_latest_snapshot(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    fixture_root = copy_manifest_and_log_fixtures(tmp_path)
    log_file = fixture_root / "logs" / "landingpage" / "app_file.log"
    log_file.write_text("first\nsecond\nthird\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        await build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace=LogWorkspace.WORKFLOW,
            manifests_dir=fixture_root / "manifests",
            since=None,
            until=None,
        )

        log_file.write_text("fourth\nfifth\nsixth\n", encoding="utf-8")

        await build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace=LogWorkspace.WORKFLOW,
            manifests_dir=fixture_root / "manifests",
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


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_workflow_archive_files_are_tracked_without_inventory_json(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    fixture_root = copy_manifest_and_log_fixtures(tmp_path)
    log_file = fixture_root / "logs" / "landingpage" / "app_file.log"
    log_file.write_text("first\nsecond\nthird\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        await build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace=LogWorkspace.WORKFLOW,
            manifests_dir=fixture_root / "manifests",
            since=None,
            until=None,
        )
        log_file.write_text("fourth\nfifth\nsixth\n", encoding="utf-8")
        await build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace=LogWorkspace.WORKFLOW,
            manifests_dir=fixture_root / "manifests",
            since=None,
            until=None,
        )

    archive_root = logs_dir / "workflow" / "landingpage" / "archive"
    archived_snapshot = next(path for path in archive_root.iterdir() if path.is_dir())

    assert not (logs_dir / "workflow" / "landingpage" / "workflow_inventory.json").exists()
    assert (logs_dir / "workflow" / "landingpage" / "latest" / "app_file.log").read_text(
        encoding="utf-8"
    ) == "fourth\nfifth\nsixth\n"
    assert (archived_snapshot / "app_file.log").read_text(encoding="utf-8") == (
        "first\nsecond\nthird\n"
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_build_collect_logs_replaces_incomplete_workflow_latest_snapshot(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"
    source_file = TEST_FILE_SOURCE_ROOT / "landingpage" / "app_file.log"
    expected_content = source_file.read_text(encoding="utf-8")

    latest_dir = logs_dir / "workflow" / "landingpage" / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / "stale.log").write_text("stale\n", encoding="utf-8")

    with override_settings(LOGS_DIR=logs_dir):
        payload = await build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace=LogWorkspace.WORKFLOW,
            since=None,
            until=None,
        )

    assert payload.snapshot_dir == str(latest_dir)
    assert not (latest_dir / "stale.log").exists()
    assert (latest_dir / "app_file.log").read_text(encoding="utf-8") == expected_content


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_session_snapshot_cleanup_uses_configured_retention_window(
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
        await build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace=LogWorkspace.SESSION,
            session_id=SESSION_ID,
            since=None,
            until=None,
        )

    assert not old_session_root.exists()
    assert recent_session_root.exists()
    assert (sessions_root / SESSION_ID / "landingpage").exists()


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_build_collect_logs_rejects_project_mismatch(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        with pytest.raises(
            ValueError,
            match="Requested project is not allowed by the authenticated caller.",
        ):
            await build_collect_logs(
                valid_access_token,
                requested_project_name="other-project",
                requested_source_keys=None,
                workspace=LogWorkspace.WORKFLOW,
                since=None,
                until=None,
            )


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_build_collect_logs_collects_full_window_without_tail_controls(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        payload = await build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace=LogWorkspace.WORKFLOW,
            since=None,
            until=None,
        )

    assert payload["warnings"] == []
    assert payload["retry_tips"] == []


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_build_collect_logs_persists_large_file_without_inline_logs(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    fixture_root = copy_manifest_and_log_fixtures(tmp_path)
    log_file = fixture_root / "logs" / "landingpage" / "app_file.log"
    full_content = "x" * 200_001
    log_file.write_text(full_content, encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        payload = await build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace=LogWorkspace.WORKFLOW,
            manifests_dir=fixture_root / "manifests",
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
    gateway = FakeDockerLogGateway()
    gateway.resolved_by_name["backend-container"] = ResolvedDockerContainer(
        name="backend-container",
        created_at=datetime(2026, 6, 8, 10, 30, tzinfo=UTC),
    )
    gateway.stream_exception = DockerLogGatewayError(
        message="Timed out collecting docker logs for backend-container.",
        error_code="docker_log_timeout",
    )

    result = LogCollectionService(docker_log_gateway=gateway).collect_source(
        definition,
        output_file=tmp_path / "backend-timeout.log",
        time_filters=DockerTimeFilters(since=None, until=None),
    )

    assert isinstance(result, SourceCollectionResult)
    assert result.status == "unavailable"
    assert "Retry with a narrower since/until window" in str(result["error"])
    assert result["retry_tips"] == [
        "Retry with a narrower since/until window to keep docker log output bounded."
    ]


def test_collect_source_uses_docker_sdk_filters(
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
    gateway = FakeDockerLogGateway()
    gateway.resolved_by_name["backend-container"] = ResolvedDockerContainer(
        name="backend-container",
        created_at=datetime(2026, 6, 8, 10, 30, tzinfo=UTC),
    )

    result = LogCollectionService(docker_log_gateway=gateway).collect_source(
        definition,
        output_file=tmp_path / "backend-filters.log",
        time_filters=LogCollectionService.validate_and_normalize_time_filters(
            sources=[definition],
            since="30m",
            until="10m",
        ),
    )

    assert result["output_file"] == str(tmp_path / "backend-filters.log")
    assert result["line_count"] == 2
    assert result["byte_count"] == 22
    assert (tmp_path / "backend-filters.log").read_text(encoding="utf-8") == (
        "log line 1\nlog line 2\n"
    )
    assert len(gateway.stream_calls) == 1
    captured = gateway.stream_calls[0][1]
    captured_since = captured["since"]
    captured_until = captured["until"]
    assert isinstance(captured_since, datetime)
    assert isinstance(captured_until, datetime)
    assert captured_since.tzinfo == UTC
    assert captured_until.tzinfo == UTC


def test_normalize_docker_time_filter_raises_specific_error() -> None:
    with pytest.raises(InvalidTimeFilterError, match="Invalid docker time filter"):
        LogCollectionService.normalize_docker_time_filter("thirty-minutes")


def test_collect_source_streams_persisted_docker_logs_without_following(
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
    gateway = FakeDockerLogGateway()
    gateway.resolved_by_name["backend-container"] = ResolvedDockerContainer(
        name="backend-container",
        created_at=datetime(2026, 6, 8, 10, 30, tzinfo=UTC),
    )

    output_file = tmp_path / "backend.log"
    result = LogCollectionService(docker_log_gateway=gateway).collect_source(
        definition,
        output_file=output_file,
        time_filters=DockerTimeFilters(
            since=LogCollectionService.normalize_docker_time_filter("30m"),
            until=LogCollectionService.normalize_docker_time_filter("10m"),
        ),
    )

    assert result["output_file"] == str(output_file)
    assert result["line_count"] == 2
    assert result["byte_count"] == 22
    assert output_file.read_text(encoding="utf-8") == "log line 1\nlog line 2\n"
    assert len(gateway.stream_calls) == 1
    assert gateway.stream_calls[0][0] == "backend-container"


def test_collect_source_uses_injected_docker_log_gateway_for_explicit_target(
    tmp_path: Path,
) -> None:
    gateway = FakeDockerLogGateway()
    gateway.resolved_by_name["backend-container"] = ResolvedDockerContainer(
        name="backend-container",
        created_at=datetime(2026, 6, 8, 10, 30, tzinfo=UTC),
    )
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

    output_file = tmp_path / "backend.log"
    result = LogCollectionService(docker_log_gateway=gateway).collect_source(
        definition,
        output_file=output_file,
        time_filters=DockerTimeFilters(since=None, until=None),
    )

    assert result["output_file"] == str(output_file)
    assert result["line_count"] == 2
    assert output_file.read_text(encoding="utf-8") == "log line 1\nlog line 2\n"
    assert gateway.name_calls == ["backend-container"]
    assert gateway.project_service_calls == []
    assert gateway.stream_calls == [("backend-container", {})]


def test_collect_source_resolves_compose_project_service_target(
    tmp_path: Path,
) -> None:
    gateway = FakeDockerLogGateway()
    gateway.resolved_by_project_service[("portfolio-stage", "be")] = ResolvedDockerContainer(
        name="portfolio-stage-be-1",
        created_at=datetime(2026, 6, 8, 10, 30, tzinfo=UTC),
    )
    definition = SourceDefinition(
        source_key="backend",
        source_type="docker",
        target="configured-container",
        compose_project="portfolio-stage",
        compose_service="be",
        description="Backend logs.",
        required=True,
        parser_type="plain_text",
        normalization_profile="backend",
        retention_class="short",
        default_noise_profile="noise",
        stream="stdout",
    )

    result = LogCollectionService(docker_log_gateway=gateway).collect_source(
        definition,
        output_file=tmp_path / "backend.log",
        time_filters=DockerTimeFilters(since=None, until=None),
    )

    assert result["target"] == "configured-container"
    assert result["line_count"] == 2
    assert gateway.name_calls == []
    assert gateway.project_service_calls == [("portfolio-stage", "be")]
    assert gateway.stream_calls == [("portfolio-stage-be-1", {})]


def test_resolve_docker_log_container_returns_stream_target_name() -> None:
    gateway = FakeDockerLogGateway()
    gateway.resolved_by_project_service[("portfolio-stage", "be")] = ResolvedDockerContainer(
        name="portfolio-stage-be-1",
        created_at=datetime(2026, 6, 8, 10, 30, tzinfo=UTC),
    )
    definition = SourceDefinition(
        source_key="backend",
        source_type="docker",
        target="configured-container",
        compose_project="portfolio-stage",
        compose_service="be",
        description="Backend logs.",
        required=True,
        parser_type="plain_text",
        normalization_profile="backend",
        retention_class="short",
        default_noise_profile="noise",
        stream="stdout",
    )

    service = LogCollectionService(docker_log_gateway=gateway)

    resolved_target = service._resolve_docker_log_container(definition)

    assert resolved_target == "portfolio-stage-be-1"


def test_collect_source_streams_persisted_file_logs_to_output_file(tmp_path) -> None:
    source_file = tmp_path / "source.log"
    source_file.write_text("log line 1\nlog line 2\n", encoding="utf-8")
    definition = SourceDefinition(
        source_key="app_file",
        source_type="file",
        target=str(source_file),
        description="Application file logs.",
        required=True,
        parser_type="plain_text",
        normalization_profile="app",
        retention_class="short",
        default_noise_profile="noise",
        stream=None,
    )

    output_file = tmp_path / "persisted.log"
    result = LogCollectionService().collect_source(
        definition,
        output_file=output_file,
        time_filters=DockerTimeFilters(since=None, until=None),
    )

    assert result["output_file"] == str(output_file)
    assert result["line_count"] == 2
    assert result["byte_count"] == output_file.stat().st_size
    assert output_file.read_text(encoding="utf-8") == "log line 1\nlog line 2\n"


def test_build_source_create_payload_uses_internal_collected_result(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    output_file = logs_dir / "workflow" / "landingpage" / "latest" / "app_file.log"
    output_file.parent.mkdir(parents=True)
    output_file.write_text("log line 1\nlog line 2\n", encoding="utf-8")
    definition = SourceDefinition(
        source_key="app_file",
        source_type="file",
        target=str(tmp_path / "source.log"),
        description="Application file logs.",
        required=True,
        parser_type="plain_text",
        normalization_profile="app",
        retention_class="short",
        default_noise_profile="noise",
        stream=None,
    )
    result = SourceCollectionResult.collected(
        definition,
        output_file=output_file,
        line_count=2,
        byte_count=22,
    )

    with override_settings(LOGS_DIR=logs_dir):
        payload = LogCollectionService()._build_source_create_payload(result=result)

    assert payload.status == "collected"
    assert payload.file == "workflow/landingpage/latest/app_file.log"
    assert payload.line_count == 2
    assert payload.error is None
    assert payload.retry_tips == []


def test_build_source_create_payload_uses_internal_unavailable_result() -> None:
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
    result = SourceCollectionResult.unavailable(
        definition,
        error="Docker Engine API is not available in the current runtime.",
        retry_tips=["Retry in a runtime where the Docker socket is mounted and reachable."],
    )

    payload = LogCollectionService()._build_source_create_payload(result=result)

    assert payload.status == "unavailable"
    assert payload.file is None
    assert payload.line_count == 0
    assert payload.error == "Docker Engine API is not available in the current runtime."
    assert payload.retry_tips == [
        "Retry in a runtime where the Docker socket is mounted and reachable."
    ]


def test_collect_source_filters_file_logs_by_time_window(tmp_path: Path) -> None:
    source_file = tmp_path / "nginx-access.log"
    source_file.write_text(
        "\n".join(
            [
                (
                    '93.105.167.111 - - [26/Jan/2026:13:35:02 +0000] "GET / HTTP/1.1" '
                    '301 169 "-" "Mozilla/5.0"'
                ),
                (
                    '10.0.0.10 - - [18/May/2026:09:38:45 +0000] "GET / HTTP/1.1" '
                    '200 8421 "-" "Mozilla/5.0"'
                ),
                "python traceback continuation",
                (
                    '10.0.0.11 - - [20/May/2026:09:38:45 +0000] "GET / HTTP/1.1" '
                    '200 8421 "-" "Mozilla/5.0"'
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    definition = SourceDefinition(
        source_key="nginx_access",
        source_type="file",
        target=str(source_file),
        description="Nginx access logs.",
        required=True,
        parser_type="nginx_json",
        normalization_profile="proxy_access",
        retention_class="short",
        default_noise_profile="web_noise",
        stream=None,
    )

    output_file = tmp_path / "persisted.log"
    result = LogCollectionService().collect_source(
        definition,
        output_file=output_file,
        time_filters=DockerTimeFilters(
            since=LogCollectionService.normalize_docker_time_filter("2026-05-18T00:00:00Z"),
            until=LogCollectionService.normalize_docker_time_filter("2026-05-19T00:00:00Z"),
            file_filters_enabled=True,
        ),
    )

    content = output_file.read_text(encoding="utf-8")
    assert result["line_count"] == 2
    assert "26/Jan/2026" not in content
    assert "18/May/2026" in content
    assert "python traceback continuation" in content
    assert "20/May/2026" not in content


def test_parse_log_line_timestamp_supports_file_log_formats() -> None:
    service = LogCollectionService()

    raw_nginx_timestamp = service.parse_log_line_timestamp(
        '93.105.167.111 - - [26/Jan/2026:13:35:02 +0000] "GET / HTTP/1.1" 301 169'
    )
    json_nginx_timestamp = service.parse_log_line_timestamp(
        '{ "time_local": "18/May/2026:09:38:45 +0000", "status": "200" }'
    )
    fail2ban_timestamp = service.parse_log_line_timestamp(
        "2026-05-18 09:38:55,771 fail2ban.filter [123]: INFO Found 203.0.113.10"
    )

    assert raw_nginx_timestamp == datetime(2026, 1, 26, 13, 35, 2, tzinfo=UTC)
    assert json_nginx_timestamp == datetime(2026, 5, 18, 9, 38, 45, tzinfo=UTC)
    assert fail2ban_timestamp == datetime(2026, 5, 18, 9, 38, 55, tzinfo=UTC)


def test_collect_source_rejects_relative_file_target(tmp_path: Path) -> None:
    definition = SourceDefinition.model_construct(
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

    result = LogCollectionService().collect_source(
        definition,
        output_file=tmp_path / "persisted.log",
        time_filters=DockerTimeFilters(since=None, until=None),
    )

    assert isinstance(result, SourceCollectionResult)
    assert result.status == "unavailable"
    assert result.error == "File source target must be an absolute path."


def test_collect_source_reports_docker_api_unavailable(
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
    gateway = FakeDockerLogGateway()
    gateway.resolve_exception = DockerLogGatewayError(
        message="Docker Engine API is not available in the current runtime.",
        error_code="docker_engine_unavailable",
    )

    result = LogCollectionService(docker_log_gateway=gateway).collect_source(
        definition,
        output_file=tmp_path / "backend-unavailable.log",
        time_filters=DockerTimeFilters(since=None, until=None),
    )

    assert isinstance(result, SourceCollectionResult)
    assert result.status == "unavailable"
    assert result["error"] == "Docker Engine API is not available in the current runtime."
    assert result["retry_tips"] == [
        "Retry in a runtime where the Docker socket is mounted and reachable."
    ]


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_build_collect_logs_uses_generated_session_id_for_session_workspace(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        payload = await build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace=LogWorkspace.SESSION,
            session_id=None,
            since=None,
            until=None,
        )

    assert payload.workspace == "session"
    session_name = Path(payload.snapshot_dir).parent.name
    assert SESSION_ID_PATTERN.fullmatch(session_name)
    assert payload.snapshot_dir == str(logs_dir / "sessions" / session_name / "landingpage")


def test_collect_logs_service_generates_session_id_for_session_workspace() -> None:
    session_id = LogCollectionService.resolve_session_id(None)

    assert SESSION_ID_PATTERN.fullmatch(session_id)
    assert len(session_id) <= 24


def test_collect_logs_service_reuses_session_id_for_session_workspace() -> None:
    existing_session_id = "calm-river-opens-a1b2"

    assert (
        LogCollectionService.resolve_session_id(f" {existing_session_id} ") == existing_session_id
    )


def test_collect_logs_service_generates_session_id_without_existing_value() -> None:
    session_id = LogCollectionService.resolve_session_id(None)

    assert SESSION_ID_PATTERN.fullmatch(session_id)
    assert len(session_id) <= 24


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_build_collect_logs_reuses_agent_chosen_session_id(
    tmp_path: Path,
    valid_access_token: AccessToken,
) -> None:
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        payload = await build_collect_logs(
            valid_access_token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace=LogWorkspace.SESSION,
            session_id=SECOND_SESSION_ID,
            since=None,
            until=None,
        )

    assert payload.workspace == "session"
    snapshot_dir = logs_dir / "sessions" / SECOND_SESSION_ID / "landingpage"
    assert payload.snapshot_dir == str(snapshot_dir)
    assert snapshot_dir.exists()
    assert not (snapshot_dir / "snapshot_metadata.json").exists()
    source_file = TEST_FILE_SOURCE_ROOT / "landingpage" / "app_file.log"
    assert (snapshot_dir / "app_file.log").read_text(encoding="utf-8") == source_file.read_text(
        encoding="utf-8"
    )
