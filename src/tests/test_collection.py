from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from docker.errors import DockerException
from fastmcp.server.auth import AccessToken
from requests import exceptions as requests_exceptions

from manifests.models import SourceDefinition
from settings import Settings
from tests.conftest import FileSourceManifestFactory
from tools.collection import (
    MAX_UNBOUNDED_FILE_BYTES,
    build_collect_logs_payload,
    collect_logs,
    collect_source,
    list_projects,
)
from tools.container_inspection import (
    list_container_directory,
    read_container_file,
    stat_container_path,
)
from utils.container_inspection_commands import ContainerPathStat


def test_build_collect_logs_payload_collects_requested_file_source(
    tmp_path,
    settings_fixture: Settings,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"

    manifest_path = file_source_manifest_factory.create(target=str(log_file))

    settings = settings_fixture.model_copy(
        update={"manifest_path": manifest_path, "logs_dir": logs_dir}
    )
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    payload = build_collect_logs_payload(
        settings,
        token,
        requested_project_name="landingpage",
        requested_source_keys=["app_file", "unknown_source"],
        save_to_files=True,
        tail_lines=2,
        timestamps=False,
        since=None,
        until=None,
    )

    assert payload["requested_project_name"] == "landingpage"
    assert payload["authorized_project_name"] == "landingpage"
    assert payload["save_to_files"] is True
    assert payload["requested_tail_lines"] == 2
    assert payload["effective_tail_lines"] == 2
    assert payload["requested_timestamps"] is False
    assert payload["requested_since"] is None
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
    assert payload["logs_by_source"] == {"app_file": "beta\ngamma"}
    latest_dir = logs_dir / "landingpage" / "latest"
    archive_dir = logs_dir / "landingpage" / "archive"

    assert payload["project_output_dir"] == str(logs_dir / "landingpage")
    assert payload["latest_output_dir"] == str(latest_dir)
    assert payload["archive_dir"] == str(archive_dir)
    assert payload.sources[0].content == "beta\ngamma"
    assert payload.sources[0].status == "collected"
    output_file = payload.sources[0].output_file
    assert output_file is not None
    assert Path(output_file).read_text(encoding="utf-8") == "beta\ngamma"
    assert (latest_dir / "collected_at.txt").exists()
    assert archive_dir.exists()


def test_build_collect_logs_payload_archives_previous_latest_snapshot(
    tmp_path,
    settings_fixture: Settings,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("first\nsecond\nthird\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"

    manifest_path = file_source_manifest_factory.create(target=str(log_file))

    settings = settings_fixture.model_copy(
        update={"manifest_path": manifest_path, "logs_dir": logs_dir}
    )
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    first_payload = build_collect_logs_payload(
        settings,
        token,
        requested_project_name="landingpage",
        requested_source_keys=["app_file"],
        save_to_files=True,
        tail_lines=1,
        timestamps=False,
        since=None,
        until=None,
    )

    log_file.write_text("fourth\nfifth\nsixth\n", encoding="utf-8")

    second_payload = build_collect_logs_payload(
        settings,
        token,
        requested_project_name="landingpage",
        requested_source_keys=["app_file"],
        save_to_files=True,
        tail_lines=1,
        timestamps=False,
        since=None,
        until=None,
    )

    latest_dir = logs_dir / "landingpage" / "latest"
    archive_root = logs_dir / "landingpage" / "archive"
    archived_snapshots = [path for path in archive_root.iterdir() if path.is_dir()]

    assert first_payload.sources[0].content == "third"
    assert second_payload.sources[0].content == "sixth"
    assert (latest_dir / "app_file.log").read_text(encoding="utf-8") == "sixth"
    assert archived_snapshots
    assert (archived_snapshots[0] / "app_file.log").read_text(encoding="utf-8") == "third"


def test_build_collect_logs_payload_rejects_project_mismatch(
    tmp_path,
    settings_fixture: Settings,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("one\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"

    manifest_path = file_source_manifest_factory.create(target=str(log_file))

    settings = settings_fixture.model_copy(
        update={"manifest_path": manifest_path, "logs_dir": logs_dir}
    )
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with pytest.raises(ValueError, match="Requested project key does not match"):
        build_collect_logs_payload(
            settings,
            token,
            requested_project_name="other-project",
            requested_source_keys=None,
            save_to_files=False,
            tail_lines=20,
            timestamps=False,
            since=None,
            until=None,
        )


def test_build_collect_logs_payload_reports_tail_line_limiting(
    tmp_path,
    settings_fixture: Settings,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("one\ntwo\nthree\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"

    manifest_path = file_source_manifest_factory.create(target=str(log_file))

    settings = settings_fixture.model_copy(
        update={"manifest_path": manifest_path, "logs_dir": logs_dir}
    )
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    payload = build_collect_logs_payload(
        settings,
        token,
        requested_project_name="landingpage",
        requested_source_keys=["app_file"],
        save_to_files=False,
        tail_lines=5000,
        timestamps=False,
        since=None,
        until=None,
    )

    assert payload["requested_tail_lines"] == 5000
    assert payload["effective_tail_lines"] == 1000
    assert payload["tail_lines_limited"] is True
    assert payload["project_output_dir"] is None
    assert payload["latest_output_dir"] is None
    assert payload["archive_dir"] is None
    assert payload["collected_at_file"] is None
    assert payload["warnings"] == [
        "Requested tail_lines=5000 exceeded the server limit of 1000. Using 1000 instead."
    ]
    assert payload["retry_tips"] == ["Retry with tail_lines <= 1000 to avoid server-side limiting."]


def test_build_collect_logs_payload_warns_when_tail_lines_is_omitted(
    tmp_path,
    settings_fixture: Settings,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("one\ntwo\nthree\n", encoding="utf-8")
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    settings = settings_fixture.model_copy(update={"manifest_path": manifest_path})
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    payload = build_collect_logs_payload(
        settings,
        token,
        requested_project_name="landingpage",
        requested_source_keys=["app_file"],
        save_to_files=False,
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


def test_read_container_file_reads_whitelisted_project_file(
    tmp_path,
    settings_fixture: Settings,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    manifest_path = file_source_manifest_factory.create(
        target="app-container",
        source_key="backend",
        source_type="docker",
        inspect_path_prefixes=["/app/"],
    )
    settings = settings_fixture.model_copy(update={"manifest_path": manifest_path})
    token = AccessToken(
        token="codex-dev-token",
        client_id="codex-agent",
        scopes=["container.files.read"],
        claims={"sub": "codex-agent", "project_key": "landingpage"},
    )

    with (
        patch(
            "tools.container_inspection.run_stat_container_path",
            return_value=ContainerPathStat(
                path="/app/VERSION",
                is_dir=False,
                size=11,
                mode=0o100644,
                modified_at="2026-04-26T10:00:00+00:00",
            ),
        ),
        patch(
            "tools.container_inspection.run_read_container_file",
            return_value=("2026.04.26\n", False),
        ),
    ):
        result = read_container_file(
            project_name="landingpage",
            source_key="backend",
            path="/app/VERSION",
            settings=settings,
            access_token=token,
        )

    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "read_container_file"
    assert payload["source_key"] == "backend"
    assert payload["container_name"] == "app-container"
    assert payload["path"] == "/app/VERSION"
    assert payload["content"] == "2026.04.26\n"
    assert payload["truncated"] is False
    assert payload["file"]["name"] == "VERSION"


def test_read_container_file_rejects_non_whitelisted_path(
    settings_fixture: Settings,
    file_source_manifest_factory: FileSourceManifestFactory,
    tmp_path,
) -> None:
    manifest_path = file_source_manifest_factory.create(
        target="app-container",
        source_key="backend",
        source_type="docker",
        inspect_path_prefixes=["/app/"],
    )
    settings = settings_fixture.model_copy(update={"manifest_path": manifest_path})
    token = AccessToken(
        token="codex-dev-token",
        client_id="codex-agent",
        scopes=["container.files.read"],
        claims={"sub": "codex-agent", "project_key": "landingpage"},
    )

    result = read_container_file(
        project_name="landingpage",
        source_key="backend",
        path="/etc/passwd",
        settings=settings,
        access_token=token,
    )

    payload = result.structured_content
    assert payload is not None

    assert payload["error_code"] == "container_path_not_allowed"
    assert payload["action"] == "read_container_file"
    assert payload["content"] == ""
    assert payload["file"] is None
    assert payload["max_bytes"] == 200000
    assert payload["path"] == "/etc/passwd"


def test_read_container_file_rejects_parent_directory_traversal(
    settings_fixture: Settings,
    file_source_manifest_factory: FileSourceManifestFactory,
    tmp_path,
) -> None:
    manifest_path = file_source_manifest_factory.create(
        target="app-container",
        source_key="backend",
        source_type="docker",
        inspect_path_prefixes=["/app/"],
    )
    settings = settings_fixture.model_copy(update={"manifest_path": manifest_path})
    token = AccessToken(
        token="codex-dev-token",
        client_id="codex-agent",
        scopes=["container.files.read"],
        claims={"sub": "codex-agent", "project_key": "landingpage"},
    )

    result = read_container_file(
        project_name="landingpage",
        source_key="backend",
        path="/app/../etc/passwd",
        settings=settings,
        access_token=token,
    )

    payload = result.structured_content
    assert payload is not None

    assert payload["error_code"] == "container_path_parent_traversal"
    assert payload["action"] == "read_container_file"
    assert payload["content"] == ""
    assert payload["file"] is None
    assert payload["path"] == "/app/../etc/passwd"


def test_list_container_directory_lists_immediate_entries(
    settings_fixture: Settings,
    file_source_manifest_factory: FileSourceManifestFactory,
    tmp_path,
) -> None:
    manifest_path = file_source_manifest_factory.create(
        target="frontend-container",
        source_key="frontend",
        source_type="docker",
        inspect_path_prefixes=["/app/"],
    )
    settings = settings_fixture.model_copy(update={"manifest_path": manifest_path})
    token = AccessToken(
        token="codex-dev-token",
        client_id="codex-agent",
        scopes=["container.files.read"],
        claims={"sub": "codex-agent", "project_key": "landingpage"},
    )

    with (
        patch(
            "tools.container_inspection.run_stat_container_path",
            return_value=ContainerPathStat(
                path="/app",
                is_dir=True,
                size=0,
                mode=0o040755,
                modified_at="2026-04-26T10:00:00+00:00",
            ),
        ),
        patch(
            "tools.container_inspection.run_list_container_directory",
            return_value=(
                [
                    ContainerPathStat(
                        path="/app/src",
                        is_dir=True,
                        size=0,
                        mode=0o040755,
                        modified_at="2026-04-26T10:00:00+00:00",
                    ),
                    ContainerPathStat(
                        path="/app/VERSION",
                        is_dir=False,
                        size=2,
                        mode=0o100644,
                        modified_at="2026-04-26T10:00:00+00:00",
                    ),
                ],
                False,
            ),
        ),
    ):
        result = list_container_directory(
            project_name="landingpage",
            source_key="frontend",
            path="/app",
            settings=settings,
            access_token=token,
        )

    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "list_container_directory"
    assert [entry["name"] for entry in payload["entries"]] == ["src", "VERSION"]
    assert payload["entries"][0]["is_dir"] is True
    assert payload["entries"][1]["is_dir"] is False


def test_stat_container_path_returns_metadata(
    settings_fixture: Settings,
    file_source_manifest_factory: FileSourceManifestFactory,
    tmp_path,
) -> None:
    manifest_path = file_source_manifest_factory.create(
        target="nginx-container",
        source_key="nginx",
        source_type="docker",
        inspect_path_prefixes=["/etc/nginx/"],
    )
    settings = settings_fixture.model_copy(update={"manifest_path": manifest_path})
    token = AccessToken(
        token="codex-dev-token",
        client_id="codex-agent",
        scopes=["container.files.read"],
        claims={"sub": "codex-agent", "project_key": "landingpage"},
    )

    with patch(
        "tools.container_inspection.run_stat_container_path",
        return_value=ContainerPathStat(
            path="/etc/nginx/nginx.conf",
            is_dir=False,
            size=23,
            mode=0o100644,
            modified_at="2026-04-26T10:00:00+00:00",
        ),
    ):
        result = stat_container_path(
            project_name="landingpage",
            source_key="nginx",
            path="/etc/nginx/nginx.conf",
            settings=settings,
            access_token=token,
        )

    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "stat_container_path"
    assert payload["stat"]["path"] == "/etc/nginx/nginx.conf"
    assert payload["stat"]["is_dir"] is False


def test_build_collect_logs_payload_reports_large_file_without_tail_lines(
    tmp_path,
    settings_fixture: Settings,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("x" * (MAX_UNBOUNDED_FILE_BYTES + 1), encoding="utf-8")
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    settings = settings_fixture.model_copy(update={"manifest_path": manifest_path})
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    payload = build_collect_logs_payload(
        settings,
        token,
        requested_project_name="landingpage",
        requested_source_keys=["app_file"],
        save_to_files=False,
        tail_lines=None,
        timestamps=False,
        since=None,
        until=None,
    )

    assert payload.sources[0].status == "unavailable"
    assert "Retry with tail_lines" in str(payload.sources[0].error)
    assert payload.sources[0].retry_tips == [
        "Retry with tail_lines <= 1000 to keep file output bounded."
    ]


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

    monkeypatch.setattr("tools.collection.docker.from_env", lambda timeout: FakeDockerClient())

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

    monkeypatch.setattr("tools.collection.docker.from_env", lambda timeout: FakeDockerClient())

    result = collect_source(
        definition,
        25,
        timestamps=True,
        since="30m",
        until="10m",
    )

    assert result["status"] == "collected"
    assert result["content"] == "log line 1\nlog line 2"
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

    monkeypatch.setattr("tools.collection.docker.from_env", fake_from_env)

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


def test_collect_logs_returns_agent_error_for_invalid_docker_time_filter(
    settings_fixture: Settings,
) -> None:
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    result = collect_logs(
        project_name="landingpage",
        source_keys=["backend"],
        save_to_files=False,
        tail_lines=20,
        timestamps=False,
        since="thirty-minutes",
        until=None,
        settings=settings_fixture,
        access_token=token,
    )
    mcp_result = result.to_mcp_result()

    assert mcp_result.isError is True
    assert mcp_result.structuredContent["error_code"] == "invalid_docker_time_filter"
    assert mcp_result.structuredContent["retry_tips"] == [
        "Retry with since/until as ISO-8601, unix seconds, or a duration like 30m, 1h, or 1d.",
        "Omit since/until if you want the current default collection range.",
    ]


def test_collect_logs_returns_agent_error_for_project_mismatch(
    tmp_path,
    settings_fixture: Settings,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("one\n", encoding="utf-8")
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    settings = settings_fixture.model_copy(update={"manifest_path": manifest_path})
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    result = collect_logs(
        project_name="other-project",
        source_keys=None,
        save_to_files=False,
        tail_lines=20,
        timestamps=False,
        since=None,
        until=None,
        settings=settings,
        access_token=token,
    )
    mcp_result = result.to_mcp_result()

    assert mcp_result.isError is True
    assert mcp_result.structuredContent["error_code"] == "project_access_mismatch"
    assert mcp_result.structuredContent["retry_tips"] == [
        "Retry with project_name equal to the project_key authorized by the current JWT.",
        "Use get_mcp_service_status to confirm the current project_key before retrying.",
    ]


def test_collect_logs_returns_agent_error_for_missing_project_claim(
    tmp_path,
    settings_fixture: Settings,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("one\n", encoding="utf-8")
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    settings = settings_fixture.model_copy(update={"manifest_path": manifest_path})
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent"},
    )

    result = collect_logs(
        project_name="landingpage",
        source_keys=None,
        save_to_files=False,
        tail_lines=20,
        timestamps=False,
        since=None,
        until=None,
        settings=settings,
        access_token=token,
    )
    mcp_result = result.to_mcp_result()

    assert mcp_result.isError is True
    assert mcp_result.structuredContent["error_code"] == "missing_project_key_claim"
    assert mcp_result.structuredContent["retry_tips"] == [
        "Retry with a JWT that includes the project_key claim for the monitored project.",
        "Use get_mcp_service_status to inspect the current caller context if needed.",
    ]


def test_list_projects_returns_manifest_backed_project_inventory(
    settings_fixture: Settings,
) -> None:
    result = list_projects(settings=settings_fixture)

    assert any(item["project_name"] == "landingpage" for item in result)
    landingpage = next(item for item in result if item["project_name"] == "landingpage")
    assert landingpage["project_summary"] == (
        "Portfolio platform with shared ingress, backend API, frontend SSR, and edge proxy logs."
    )
    assert landingpage["manifest_file"] == "landingpage.json"
    assert "backend" in landingpage["source_keys"]
    assert "docker" in landingpage["source_types"]
    assert landingpage["docker_sources_available"] is True
    assert landingpage["file_sources_available"] is False


def test_list_projects_returns_multiple_manifest_backed_projects(
    tmp_path,
    settings_fixture: Settings,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    alpha_log = tmp_path / "alpha.log"
    alpha_log.write_text("alpha\n", encoding="utf-8")
    beta_log = tmp_path / "beta.log"
    beta_log.write_text("beta\n", encoding="utf-8")

    file_source_manifest_factory.create(
        target=str(alpha_log),
        project_name="alpha",
        project_summary="Alpha project summary.",
    )
    file_source_manifest_factory.create(
        target=str(beta_log),
        project_name="beta",
        project_summary="Beta project summary.",
    )
    settings = settings_fixture.model_copy(update={"manifest_path": tmp_path / "alpha.json"})

    result = list_projects(settings=settings)

    assert [item["project_name"] for item in result] == ["alpha", "beta"]
    assert result[0]["project_summary"] == "Alpha project summary."
    assert result[1]["project_summary"] == "Beta project summary."
