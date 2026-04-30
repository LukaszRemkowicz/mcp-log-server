from __future__ import annotations

from fastmcp.server.auth import AccessToken

from tests.conftest import FileSourceManifestFactory, override_settings
from tools.collection import collect_logs, list_projects


def test_collect_logs_returns_agent_error_for_invalid_docker_time_filter() -> None:
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings():
        result = collect_logs(
            project_name="landingpage",
            source_keys=["backend"],
            tail_lines=20,
            timestamps=False,
            since="thirty-minutes",
            until=None,
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
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("one\n", encoding="utf-8")
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path):
        result = collect_logs(
            project_name="other-project",
            source_keys=None,
            tail_lines=20,
            timestamps=False,
            since=None,
            until=None,
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
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("one\n", encoding="utf-8")
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent"},
    )

    with override_settings(MANIFEST_PATH=manifest_path):
        result = collect_logs(
            project_name="landingpage",
            source_keys=None,
            tail_lines=20,
            timestamps=False,
            since=None,
            until=None,
            access_token=token,
        )
    mcp_result = result.to_mcp_result()

    assert mcp_result.isError is True
    assert mcp_result.structuredContent["error_code"] == "missing_project_key_claim"
    assert mcp_result.structuredContent["retry_tips"] == [
        "Retry with a JWT that includes the project_key claim for the monitored project.",
        "Use get_mcp_service_status to inspect the current caller context if needed.",
    ]


def test_collect_logs_returns_agent_error_for_missing_session_id(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("one\n", encoding="utf-8")
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path):
        result = collect_logs(
            project_name="landingpage",
            source_keys=["app_file"],
            workspace="session",
            session_id=None,
            tail_lines=20,
            timestamps=False,
            since=None,
            until=None,
            access_token=token,
        )
    mcp_result = result.to_mcp_result()

    assert mcp_result.isError is True
    assert mcp_result.structuredContent["error_code"] == "missing_session_id"


def test_list_projects_returns_manifest_backed_project_inventory() -> None:
    with override_settings():
        result = list_projects()

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
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    alpha_log = tmp_path / "alpha.log"
    alpha_log.write_text("alpha\n", encoding="utf-8")
    beta_log = tmp_path / "beta.log"
    beta_log.write_text("beta\n", encoding="utf-8")

    file_source_manifest_factory.create(
        target=str(alpha_log),
        project_name="alpha\nshared match\nomega\n",
        project_summary="Alpha project summary.",
    )
    file_source_manifest_factory.create(
        target=str(beta_log),
        project_name="beta",
        project_summary="Beta project summary.",
    )
    with override_settings(MANIFEST_PATH=tmp_path / "alpha.json"):
        result = list_projects()

    assert [item["project_name"] for item in result] == ["alpha\nshared match\nomega\n", "beta"]
    assert result[0]["project_summary"] == "Alpha project summary."
    assert result[1]["project_summary"] == "Beta project summary."
