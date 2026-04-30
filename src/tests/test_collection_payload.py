from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docker.errors import DockerException
from fastmcp.server.auth import AccessToken
from requests import exceptions as requests_exceptions

import conf
from manifests.models import SourceDefinition
from services.log_collection import MAX_INLINE_LOG_BYTES, LogCollectionService
from services.log_snapshots import LogSnapshotService
from services.log_source_collection import LogSourceCollectionService
from tests.conftest import FileSourceManifestFactory, override_settings
from tools.models import SnapshotWorkspace


def build_collect_logs_payload(
    token: AccessToken,
    *,
    requested_project_name: str | None,
    requested_source_keys: list[str] | None,
    workspace: SnapshotWorkspace,
    session_id: str | None = None,
    tail_lines: int | None,
    timestamps: bool,
    since: str | None,
    until: str | None,
):
    """Assemble a collection payload directly through the real services for tests."""

    settings = conf.settings
    source_collection_service = LogSourceCollectionService()
    collection_service = LogCollectionService(
        settings,
        token,
        snapshot_service=LogSnapshotService(settings, token),
        source_collector=source_collection_service.collect_source,
        tail_line_limiter=source_collection_service.limit_tail_lines,
    )
    return collection_service.build_payload(
        requested_project_name=requested_project_name,
        requested_source_keys=requested_source_keys,
        workspace=workspace,
        session_id=session_id,
        tail_lines=tail_lines,
        timestamps=timestamps,
        since=since,
        until=until,
    )


def collect_source(
    definition: SourceDefinition,
    tail_lines: int | None,
    *,
    timestamps: bool,
    since: str | None,
    until: str | None,
):
    """Collect one source directly through the deterministic adapter service for tests."""

    return LogSourceCollectionService().collect_source(
        definition,
        tail_lines,
        timestamps=timestamps,
        since=since,
        until=until,
    )


def test_build_collect_logs_payload_collects_requested_file_source(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"

    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path, LOGS_DIR=logs_dir):
        payload = build_collect_logs_payload(
            token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file", "unknown_source"],
            workspace="workflow",
            tail_lines=2,
            timestamps=False,
            since=None,
            until=None,
        )

    assert payload["requested_project_name"] == "landingpage"
    assert payload["authorized_project_name"] == "landingpage"
    assert payload["workspace"] == "workflow"
    assert payload["requested_tail_lines"] == 2
    assert payload["effective_tail_lines"] == 2
    assert payload["requested_timestamps"] is False
    assert payload["requested_since"] == "24h"
    assert payload["requested_until"] is None
    assert payload["tail_lines_limited"] is False
    assert payload["resolved_source_keys"] == ["app_file"]
    assert payload["unknown_requested_source_keys"] == ["unknown_source"]
    assert payload["warnings"] == [
        "Some requested source_keys were not found in the configured manifest: unknown_source."
    ]
    assert payload["retry_tips"] == [
        "Retry with only source_keys returned by the manifest-backed project configuration."
    ]
    assert payload["logs_by_source"] == {"app_file": "beta\ngamma\n"}
    latest_dir = logs_dir / "landingpage" / "workflow" / "latest"
    archive_dir = logs_dir / "landingpage" / "workflow" / "archive"

    assert payload.project_output_dir == str(logs_dir / "landingpage")
    assert payload.latest_output_dir == str(latest_dir)
    assert payload.archive_dir == str(archive_dir)
    assert payload.snapshot_dir == str(latest_dir)
    assert payload.persisted is True
    assert payload.snapshot_id.startswith("workflow_")
    assert Path(payload.metadata_file).exists()
    assert payload.sources[0].content == "beta\ngamma\n"
    assert payload.sources[0].status == "collected"
    output_file = payload.sources[0].output_file
    assert output_file is not None
    assert Path(output_file).read_text(encoding="utf-8") == "beta\ngamma\n"
    assert (latest_dir / "collected_at.txt").exists()
    assert archive_dir.exists()


def test_build_collect_logs_payload_uses_runtime_default_log_window(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("alpha\nbeta\n", encoding="utf-8")
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path, DEFAULT_LOG_WINDOW="12h"):
        payload = build_collect_logs_payload(
            token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            tail_lines=None,
            timestamps=False,
            since=None,
            until=None,
        )

    assert payload["requested_since"] == "12h"


def test_build_collect_logs_payload_archives_previous_latest_snapshot(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("first\nsecond\nthird\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"

    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path, LOGS_DIR=logs_dir):
        first_payload = build_collect_logs_payload(
            token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            tail_lines=1,
            timestamps=False,
            since=None,
            until=None,
        )

        log_file.write_text("fourth\nfifth\nsixth\n", encoding="utf-8")

        second_payload = build_collect_logs_payload(
            token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            tail_lines=1,
            timestamps=False,
            since=None,
            until=None,
        )

    latest_dir = logs_dir / "landingpage" / "workflow" / "latest"
    archive_root = logs_dir / "landingpage" / "workflow" / "archive"
    archived_snapshots = [path for path in archive_root.iterdir() if path.is_dir()]

    assert first_payload.sources[0].content == "third\n"
    assert second_payload.sources[0].content == "sixth\n"
    assert (latest_dir / "app_file.log").read_text(encoding="utf-8") == "sixth\n"
    assert archived_snapshots
    assert (archived_snapshots[0] / "app_file.log").read_text(encoding="utf-8") == "third\n"


def test_archived_workflow_snapshot_metadata_points_to_archived_files(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("first\nsecond\nthird\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path, LOGS_DIR=logs_dir):
        build_collect_logs_payload(
            token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            tail_lines=1,
            timestamps=False,
            since=None,
            until=None,
        )
        log_file.write_text("fourth\nfifth\nsixth\n", encoding="utf-8")
        build_collect_logs_payload(
            token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            tail_lines=1,
            timestamps=False,
            since=None,
            until=None,
        )

    archive_root = logs_dir / "landingpage" / "workflow" / "archive"
    archived_snapshot = next(path for path in archive_root.iterdir() if path.is_dir())
    archived_metadata = json.loads(
        (archived_snapshot / "snapshot_metadata.json").read_text(encoding="utf-8")
    )

    assert archived_metadata["files"][0]["output_file"] == str(archived_snapshot / "app_file.log")


def test_session_snapshot_cleanup_uses_configured_retention_window(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("one\ntwo\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    sessions_root = logs_dir / "landingpage" / "sessions"
    old_snapshot = sessions_root / "session_old"
    old_snapshot.mkdir(parents=True, exist_ok=True)
    old_file = old_snapshot / "backend.log"
    old_file.write_text("old\n", encoding="utf-8")
    old_timestamp = (datetime.now(UTC) - timedelta(minutes=11)).timestamp()
    old_snapshot.touch()
    old_file.touch()
    os.utime(old_snapshot, (old_timestamp, old_timestamp))
    os.utime(old_file, (old_timestamp, old_timestamp))

    recent_snapshot = sessions_root / "session_recent"
    recent_snapshot.mkdir(parents=True, exist_ok=True)
    recent_file = recent_snapshot / "backend.log"
    recent_file.write_text("recent\n", encoding="utf-8")

    with override_settings(
        MANIFEST_PATH=manifest_path,
        LOGS_DIR=logs_dir,
        LOG_SNAPSHOT_RETENTION="10m",
    ):
        payload = build_collect_logs_payload(
            token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="session",
            session_id="cleanup-session",
            tail_lines=1,
            timestamps=False,
            since=None,
            until=None,
        )

    assert not old_snapshot.exists()
    assert recent_snapshot.exists()
    assert (sessions_root / payload.snapshot_id).exists()


def test_build_collect_logs_payload_rejects_project_mismatch(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("one\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"

    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path, LOGS_DIR=logs_dir):
        with pytest.raises(ValueError, match="Requested project key does not match"):
            build_collect_logs_payload(
                token,
                requested_project_name="other-project",
                requested_source_keys=None,
                workspace="workflow",
                tail_lines=20,
                timestamps=False,
                since=None,
                until=None,
            )


def test_build_collect_logs_payload_reports_tail_line_limiting(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("one\ntwo\nthree\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"

    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path, LOGS_DIR=logs_dir):
        payload = build_collect_logs_payload(
            token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            tail_lines=5000,
            timestamps=False,
            since=None,
            until=None,
        )

    assert payload["requested_tail_lines"] == 5000
    assert payload["effective_tail_lines"] == 1000
    assert payload["tail_lines_limited"] is True
    assert payload["project_output_dir"] == str(logs_dir / "landingpage")
    assert payload["latest_output_dir"] == str(logs_dir / "landingpage" / "workflow" / "latest")
    assert payload["archive_dir"] == str(logs_dir / "landingpage" / "workflow" / "archive")
    assert payload["snapshot_dir"] == str(logs_dir / "landingpage" / "workflow" / "latest")
    assert payload["collected_at_file"] is not None
    assert payload["warnings"] == [
        "Requested tail_lines=5000 exceeded the server limit of 1000. Using 1000 instead."
    ]
    assert payload["retry_tips"] == ["Retry with tail_lines <= 1000 to avoid server-side limiting."]


def test_build_collect_logs_payload_warns_when_tail_lines_is_omitted(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("one\ntwo\nthree\n", encoding="utf-8")
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path):
        payload = build_collect_logs_payload(
            token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            tail_lines=None,
            timestamps=False,
            since=None,
            until=None,
        )

    assert payload["requested_tail_lines"] is None
    assert payload["effective_tail_lines"] is None
    assert payload["tail_lines_limited"] is False
    assert payload["warnings"] == [
        "No tail_lines value was provided. Full source output will be requested where supported."
    ]
    assert payload["retry_tips"] == [
        (
            "Retry with tail_lines to keep docker and file collection bounded if "
            "a source is slow or large."
        )
    ]
    assert payload["logs_by_source"] == {"app_file": "one\ntwo\nthree\n"}
    assert payload.sources[0].content == "one\ntwo\nthree\n"


def test_build_collect_logs_payload_auto_persists_large_file_without_tail_lines(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    full_content = "x" * (MAX_INLINE_LOG_BYTES + 1)
    log_file.write_text(full_content, encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path, LOGS_DIR=logs_dir):
        payload = build_collect_logs_payload(
            token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="workflow",
            tail_lines=None,
            timestamps=False,
            since=None,
            until=None,
        )

    assert payload.sources[0].status == "collected"
    assert payload.sources[0].content_truncated is True
    assert payload.sources[0].byte_count == len(full_content.encode("utf-8"))
    assert payload.sources[0].output_file is not None
    assert Path(payload.sources[0].output_file).read_text(encoding="utf-8") == full_content
    assert payload["project_output_dir"] == str(logs_dir / "landingpage")
    assert payload["latest_output_dir"] == str(logs_dir / "landingpage" / "workflow" / "latest")
    assert any("only a preview was returned" in warning for warning in payload.warnings)
    assert payload.logs_by_source["app_file"] == payload.sources[0].content


def test_collect_source_reports_docker_timeout_without_tail_lines_tip(monkeypatch) -> None:
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

    class FakeContainer:
        def logs(self, **kwargs: object) -> bytes:
            raise requests_exceptions.Timeout()

    class FakeContainerCollection:
        @staticmethod
        def get(name: str) -> FakeContainer:
            assert name == "backend-container"
            return FakeContainer()

    class FakeDockerClient:
        containers = FakeContainerCollection()

    monkeypatch.setattr(
        "services.log_source_collection.docker.from_env",
        lambda timeout: FakeDockerClient(),
    )

    result = collect_source(
        definition,
        None,
        timestamps=False,
        since=None,
        until=None,
    )

    assert result["status"] == "unavailable"
    assert "Retry with tail_lines" in str(result["error"])
    assert result["retry_tips"] == [
        "Retry with tail_lines <= 1000 to keep docker log output bounded."
    ]


def test_collect_source_uses_docker_sdk_filters(monkeypatch) -> None:
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
    captured: dict[str, object] = {}

    class FakeContainer:
        def logs(self, **kwargs: object) -> bytes:
            captured.update(kwargs)
            return b"log line 1\nlog line 2\n"

    class FakeContainerCollection:
        @staticmethod
        def get(name: str) -> FakeContainer:
            assert name == "backend-container"
            return FakeContainer()

    class FakeDockerClient:
        containers = FakeContainerCollection()

    monkeypatch.setattr(
        "services.log_source_collection.docker.from_env",
        lambda timeout: FakeDockerClient(),
    )

    result = collect_source(
        definition,
        25,
        timestamps=True,
        since="30m",
        until="10m",
    )

    assert result["status"] == "collected"
    assert result["content"] == "log line 1\nlog line 2\n"
    assert captured["timestamps"] is True
    assert captured["stdout"] is True
    assert captured["stderr"] is True
    assert captured["tail"] == 25
    assert isinstance(captured["since"], datetime)
    assert isinstance(captured["until"], datetime)
    assert captured["since"].tzinfo == UTC
    assert captured["until"].tzinfo == UTC


def test_collect_source_reports_docker_api_unavailable(monkeypatch) -> None:
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

    monkeypatch.setattr("services.log_source_collection.docker.from_env", fake_from_env)

    result = collect_source(
        definition,
        25,
        timestamps=False,
        since=None,
        until=None,
    )

    assert result["status"] == "unavailable"
    assert result["error"] == "Docker Engine API is not available in the current runtime."
    assert result["retry_tips"] == [
        "Retry in a runtime where the Docker socket is mounted and reachable."
    ]


def test_build_collect_logs_payload_requires_agent_chosen_session_id(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("one\ntwo\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path, LOGS_DIR=logs_dir):
        with pytest.raises(ValueError, match="session_id is required"):
            build_collect_logs_payload(
                token,
                requested_project_name="landingpage",
                requested_source_keys=["app_file"],
                workspace="session",
                session_id=None,
                tail_lines=1,
                timestamps=False,
                since=None,
                until=None,
            )


def test_build_collect_logs_payload_reuses_agent_chosen_session_id(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("one\ntwo\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path, LOGS_DIR=logs_dir):
        payload = build_collect_logs_payload(
            token,
            requested_project_name="landingpage",
            requested_source_keys=["app_file"],
            workspace="session",
            session_id="agent-session-1",
            tail_lines=1,
            timestamps=False,
            since=None,
            until=None,
        )

    snapshot_dir = logs_dir / "landingpage" / "sessions" / "agent-session-1"
    assert payload.workspace == "session"
    assert payload.session_id == "agent-session-1"
    assert payload.snapshot_id == "agent-session-1"
    assert payload.snapshot_dir == str(snapshot_dir)
    assert payload.latest_output_dir is None
    assert payload.archive_dir is None
    assert snapshot_dir.exists()
    assert (snapshot_dir / "app_file.log").read_text(encoding="utf-8") == "two\n"
