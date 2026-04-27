from __future__ import annotations

import json
from collections.abc import Callable
from unittest.mock import patch

import pytest

from auth.scopes import (
    CONTAINER_FILES_READ_SCOPE,
    LOGS_COLLECT_SCOPE,
    MCP_HEALTH_READ_SCOPE,
    MCP_STATUS_READ_SCOPE,
    PROJECTS_READ_SCOPE,
    WORKFLOW_BOOTSTRAP_SCOPE,
    WORKFLOW_SKILLS_READ_SCOPE,
)
from settings import Settings
from tests.conftest import CollectLogsRequestFactory, FileSourceManifestFactory, JsonRpcFixture
from utils.container_inspection_commands import ContainerPathStat


@pytest.mark.parametrize(
    ("subject", "scopes", "client_id", "expected_present", "expected_absent"),
    [
        (
            "workflow-agent",
            [
                LOGS_COLLECT_SCOPE,
                PROJECTS_READ_SCOPE,
                WORKFLOW_BOOTSTRAP_SCOPE,
                WORKFLOW_SKILLS_READ_SCOPE,
                MCP_STATUS_READ_SCOPE,
                MCP_HEALTH_READ_SCOPE,
            ],
            "workflow-agent",
            {
                "analyze_daily_log_bundle",
                "collect_logs",
                "list_log_snapshot_files",
                "read_log_snapshot_file",
                "grep_log_snapshot",
                "list_projects",
                "get_mcp_service_status",
                "get_mcp_health_check",
            },
            {
                "read_container_file",
                "stat_container_path",
                "list_container_directory",
            },
        ),
        (
            "codex-agent",
            [
                PROJECTS_READ_SCOPE,
                LOGS_COLLECT_SCOPE,
                CONTAINER_FILES_READ_SCOPE,
                MCP_STATUS_READ_SCOPE,
                MCP_HEALTH_READ_SCOPE,
            ],
            "codex-agent",
            {
                "collect_logs",
                "list_log_snapshot_files",
                "read_log_snapshot_file",
                "grep_log_snapshot",
                "list_projects",
                "read_container_file",
                "stat_container_path",
                "list_container_directory",
                "get_mcp_service_status",
                "get_mcp_health_check",
            },
            {
                "analyze_daily_log_bundle",
            },
        ),
    ],
)
def test_tools_list_filters_visible_tools_per_jwt(
    create_test_jwt_token: Callable[[str, list[str], str], str],
    jsonrpc: JsonRpcFixture,
    subject: str,
    scopes: list[str],
    client_id: str,
    expected_present: set[str],
    expected_absent: set[str],
) -> None:
    token = create_test_jwt_token(
        subject,
        scopes,
        client_id,
    )

    response = jsonrpc.post(
        token=token,
        data={"jsonrpc": "2.0", "id": "2", "method": "tools/list", "params": {}},
    )

    tool_names = {tool["name"] for tool in response.json()["result"]["tools"]}

    assert expected_present <= tool_names
    assert tool_names.isdisjoint(expected_absent)


def test_analyze_daily_log_bundle_api_returns_structured_workflow_bootstrap(
    create_test_jwt_token: Callable[[str, list[str], str], str],
    jsonrpc: JsonRpcFixture,
) -> None:
    workflow_token = create_test_jwt_token(
        "workflow-agent",
        [
            LOGS_COLLECT_SCOPE,
            PROJECTS_READ_SCOPE,
            WORKFLOW_BOOTSTRAP_SCOPE,
            WORKFLOW_SKILLS_READ_SCOPE,
            MCP_STATUS_READ_SCOPE,
            MCP_HEALTH_READ_SCOPE,
        ],
        "workflow-agent",
    )

    response = jsonrpc.post(
        token=workflow_token,
        data={
            "jsonrpc": "2.0",
            "id": "3",
            "method": "tools/call",
            "params": {"name": "analyze_daily_log_bundle", "arguments": {}},
        },
    )

    payload = response.json()["result"]["structuredContent"]
    content = response.json()["result"]["content"]

    assert response.status_code == 200
    assert content == []
    assert payload["workflow_name"] == "analyze_daily_log_bundle"
    assert "Log Summary Instructions" in payload["prompt"]
    assert any(
        item["resource_uri"] == "skill://workflow/severity_guide"
        for item in payload["mandatory_skills"]
    )
    assert any(
        item["resource_uri"] == "skill://workflow/bot_detection"
        for item in payload["optional_skills"]
    )
    assert any(item["tool_name"] == "collect_logs" for item in payload["tools"])
    assert any(item["tool_name"] == "list_log_snapshot_files" for item in payload["tools"])
    assert any(item["tool_name"] == "read_log_snapshot_file" for item in payload["tools"])
    assert any(item["tool_name"] == "grep_log_snapshot" for item in payload["tools"])
    assert any(item["tool_name"] == "list_projects" for item in payload["tools"])
    assert any(item["tool_name"] == "get_mcp_service_status" for item in payload["tools"])
    assert any(item["tool_name"] == "get_mcp_health_check" for item in payload["tools"])
    collect_logs_tool = next(
        item for item in payload["tools"] if item["tool_name"] == "collect_logs"
    )
    assert any(argument["name"] == "project_name" for argument in collect_logs_tool["arguments"])
    assert any(argument["name"] == "source_keys" for argument in collect_logs_tool["arguments"])
    assert any(argument["name"] == "session_id" for argument in collect_logs_tool["arguments"])


def test_collect_logs_api_returns_requested_and_resolved_file_sources(
    tmp_path,
    settings_fixture: Settings,
    create_test_jwt_token: Callable[[str, list[str], str], str],
    file_source_manifest_factory: FileSourceManifestFactory,
    collect_logs_request_factory: CollectLogsRequestFactory,
    jsonrpc: JsonRpcFixture,
) -> None:
    log_file = tmp_path / "application.log"
    log_file.write_text("line 1\nline 2\nline 3\n", encoding="utf-8")
    manifest_path = file_source_manifest_factory.create(target=str(log_file))

    logs_dir = tmp_path / "collected-logs"
    settings = settings_fixture.model_copy(
        update={"MANIFEST_PATH": manifest_path, "LOGS_DIR": logs_dir}
    )
    token = create_test_jwt_token(
        "workflow-agent",
        [LOGS_COLLECT_SCOPE, PROJECTS_READ_SCOPE],
        "workflow-agent",
    )

    with jsonrpc.with_settings(settings) as custom_jsonrpc:
        response = custom_jsonrpc.post(
            token=token,
            data=collect_logs_request_factory.create(
                source_keys=["app_file", "missing_source"],
                tail_lines=2,
            ),
        )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert payload["requested_project_name"] == "landingpage"
    assert payload["authorized_project_name"] == "landingpage"
    assert payload["workspace"] == "workflow"
    assert payload["requested_source_keys"] == ["app_file", "missing_source"]
    assert payload["requested_tail_lines"] == 2
    assert payload["effective_tail_lines"] == 2
    assert payload["requested_timestamps"] is False
    assert payload["requested_since"] == "24h"
    assert payload["requested_until"] is None
    assert payload["tail_lines_limited"] is False
    assert payload["unknown_requested_source_keys"] == ["missing_source"]
    assert payload["resolved_source_keys"] == ["app_file"]
    assert payload["warnings"] == [
        "Some requested source_keys were not found in the configured manifest: missing_source."
    ]
    assert payload["retry_tips"] == [
        "Retry with only source_keys returned by the manifest-backed project configuration."
    ]
    assert payload["logs_by_source"] == {"app_file": "line 2\nline 3\n"}
    assert payload["project_output_dir"] == str(logs_dir / "landingpage")
    assert payload["latest_output_dir"] == str(logs_dir / "landingpage" / "workflow" / "latest")
    assert payload["archive_dir"] == str(logs_dir / "landingpage" / "workflow" / "archive")
    assert payload["snapshot_dir"] == str(logs_dir / "landingpage" / "workflow" / "latest")
    assert payload["persisted"] is True
    assert payload["sources"][0]["source_key"] == "app_file"
    assert payload["sources"][0]["status"] == "collected"
    assert payload["sources"][0]["output_file"] == str(
        logs_dir / "landingpage" / "workflow" / "latest" / "app_file.log"
    )
    assert payload["sources"][0]["content_truncated"] is False
    assert payload["sources"][0]["byte_count"] == len(b"line 2\nline 3\n")
    assert payload["sources"][0]["content"] == "line 2\nline 3\n"


def test_list_projects_api_returns_manifest_backed_projects(
    create_test_jwt_token: Callable[[str, list[str], str], str],
    jsonrpc: JsonRpcFixture,
) -> None:
    token = create_test_jwt_token(
        "workflow-agent",
        [PROJECTS_READ_SCOPE],
        "workflow-agent",
    )

    response = jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "list-projects",
            "method": "tools/call",
            "params": {"name": "list_projects", "arguments": {}},
        },
    )

    payload = response.json()["result"]["structuredContent"]["result"]

    assert response.status_code == 200
    assert any(item["project_name"] == "landingpage" for item in payload)
    landingpage = next(item for item in payload if item["project_name"] == "landingpage")
    assert landingpage["project_summary"] == (
        "Portfolio platform with shared ingress, backend API, frontend SSR, and edge proxy logs."
    )
    assert landingpage["manifest_file"] == "landingpage.json"
    assert "docker" in landingpage["source_types"]


def test_read_container_file_api_returns_file_contents(
    tmp_path,
    settings_fixture: Settings,
    create_test_jwt_token: Callable[[str, list[str], str], str],
    file_source_manifest_factory: FileSourceManifestFactory,
    jsonrpc: JsonRpcFixture,
) -> None:
    manifest_path = file_source_manifest_factory.create(
        target="backend-container",
        source_key="backend",
        source_type="docker",
        inspect_path_prefixes=["/app/"],
    )
    settings = settings_fixture.model_copy(update={"MANIFEST_PATH": manifest_path})
    token = create_test_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
    )

    with (
        patch(
            "tools.container_inspection.run_stat_container_path",
            return_value=ContainerPathStat(
                path="/app/VERSION",
                is_dir=False,
                size=12,
                mode=0o100644,
                modified_at="2026-04-26T10:00:00+00:00",
            ),
        ),
        patch(
            "tools.container_inspection.run_read_container_file",
            return_value=("release-123\n", False),
        ),
    ):
        with jsonrpc.with_settings(settings) as custom_jsonrpc:
            response = custom_jsonrpc.post(
                token=token,
                data={
                    "jsonrpc": "2.0",
                    "id": "read-container-file",
                    "method": "tools/call",
                    "params": {
                        "name": "read_container_file",
                        "arguments": {
                            "project_name": "landingpage",
                            "source_key": "backend",
                            "path": "/app/VERSION",
                        },
                    },
                },
            )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert payload["action"] == "read_container_file"
    assert payload["content"] == "release-123\n"
    assert payload["file"]["name"] == "VERSION"


def test_list_projects_api_returns_multiple_manifest_backed_projects(
    tmp_path,
    settings_fixture: Settings,
    create_test_jwt_token: Callable[[str, list[str], str], str],
    file_source_manifest_factory: FileSourceManifestFactory,
    jsonrpc: JsonRpcFixture,
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
    settings = settings_fixture.model_copy(update={"MANIFEST_PATH": tmp_path / "alpha.json"})
    token = create_test_jwt_token(
        "workflow-agent",
        [PROJECTS_READ_SCOPE],
        "workflow-agent",
    )

    with jsonrpc.with_settings(settings) as custom_jsonrpc:
        response = custom_jsonrpc.post(
            token=token,
            data={
                "jsonrpc": "2.0",
                "id": "list-projects-multi",
                "method": "tools/call",
                "params": {"name": "list_projects", "arguments": {}},
            },
        )

    payload = response.json()["result"]["structuredContent"]["result"]

    assert [item["project_name"] for item in payload] == ["alpha", "beta"]
    assert payload[0]["project_summary"] == "Alpha project summary."
    assert payload[1]["project_summary"] == "Beta project summary."


def test_collect_logs_api_returns_agent_error_for_project_mismatch(
    create_test_jwt_token: Callable[[str, list[str], str], str],
    collect_logs_request_factory: CollectLogsRequestFactory,
    jsonrpc: JsonRpcFixture,
) -> None:
    token = create_test_jwt_token(
        "workflow-agent",
        [LOGS_COLLECT_SCOPE, PROJECTS_READ_SCOPE],
        "workflow-agent",
    )

    response = jsonrpc.post(
        token=token,
        data=collect_logs_request_factory.create(project_name="other-project"),
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert payload["status"] == "error"
    assert payload["error_code"] == "project_access_mismatch"
    assert payload["retry_tips"] == [
        "Retry with project_name equal to the project_key authorized by the current JWT.",
        "Use get_mcp_service_status to confirm the current project_key before retrying.",
    ]


def test_workflow_skill_resource_read_api_returns_skill_contents(
    create_test_jwt_token: Callable[[str, list[str], str], str],
    jsonrpc: JsonRpcFixture,
) -> None:
    workflow_token = create_test_jwt_token(
        "workflow-agent",
        [WORKFLOW_SKILLS_READ_SCOPE],
        "workflow-agent",
    )

    response = jsonrpc.post(
        token=workflow_token,
        data={
            "jsonrpc": "2.0",
            "id": "4",
            "method": "resources/read",
            "params": {"uri": "skill://workflow/severity_guide"},
        },
    )

    contents = response.json()["result"]["contents"]

    assert response.status_code == 200
    assert contents[0]["uri"] == "skill://workflow/severity_guide"
    assert "SEVERITY CLASSIFICATION" in contents[0]["text"]


def test_resources_list_shows_concrete_workflow_skill_resources(
    create_test_jwt_token: Callable[[str, list[str], str], str],
    jsonrpc: JsonRpcFixture,
) -> None:
    workflow_token = create_test_jwt_token(
        "workflow-agent",
        [WORKFLOW_SKILLS_READ_SCOPE],
        "workflow-agent",
    )

    response = jsonrpc.post(
        token=workflow_token,
        data={
            "jsonrpc": "2.0",
            "id": "4b",
            "method": "resources/list",
            "params": {},
        },
    )

    resources = response.json()["result"]["resources"]
    resource_uris = [resource["uri"] for resource in resources]

    assert response.status_code == 200
    assert "skill://workflow/project_context" in resource_uris
    assert "skill://workflow/severity_guide" in resource_uris
    assert "skill://workflow/bot_detection" in resource_uris


def test_resource_templates_list_exposes_workflow_skill_template(
    create_test_jwt_token: Callable[[str, list[str], str], str],
    jsonrpc: JsonRpcFixture,
) -> None:
    workflow_token = create_test_jwt_token(
        "workflow-agent",
        [WORKFLOW_SKILLS_READ_SCOPE],
        "workflow-agent",
    )

    response = jsonrpc.post(
        token=workflow_token,
        data={
            "jsonrpc": "2.0",
            "id": "4c",
            "method": "resources/templates/list",
            "params": {},
        },
    )

    templates = response.json()["result"]["resourceTemplates"]

    assert response.status_code == 200
    assert templates == [
        {
            "name": "workflow_skill_resource",
            "uriTemplate": "skill://workflow/{skill_name}",
            "description": "Read one workflow skill as an MCP resource.",
            "mimeType": "text/plain",
            "_meta": {"fastmcp": {"tags": []}},
        }
    ]


def test_invalid_workflow_skill_resource_returns_agent_guidance(
    create_test_jwt_token: Callable[[str, list[str], str], str],
    jsonrpc: JsonRpcFixture,
) -> None:
    workflow_token = create_test_jwt_token(
        "workflow-agent",
        [WORKFLOW_SKILLS_READ_SCOPE],
        "workflow-agent",
    )

    response = jsonrpc.post(
        token=workflow_token,
        data={
            "jsonrpc": "2.0",
            "id": "4d",
            "method": "resources/read",
            "params": {"uri": "skill://workflow/not_a_real_skill"},
        },
    )

    contents = response.json()["result"]["contents"]
    payload = json.loads(contents[0]["text"])

    assert response.status_code == 200
    assert contents[0]["uri"] == "skill://workflow/not_a_real_skill"
    assert contents[0]["mimeType"] == "application/json"
    assert payload["error_code"] == "unknown_workflow_skill"
    assert payload["retry_tips"] == [
        "Retry with one of the skill://workflow/... URIs returned by resources/list.",
        "Call analyze_daily_log_bundle to get the workflow skill inventory before retrying.",
    ]


def test_codex_cannot_access_workflow_components(
    create_test_jwt_token: Callable[[str, list[str], str], str],
    jsonrpc: JsonRpcFixture,
) -> None:
    codex_token = create_test_jwt_token(
        "codex-agent",
        [PROJECTS_READ_SCOPE, LOGS_COLLECT_SCOPE, MCP_STATUS_READ_SCOPE, MCP_HEALTH_READ_SCOPE],
        "codex-agent",
    )

    tool_response = jsonrpc.post(
        token=codex_token,
        data={
            "jsonrpc": "2.0",
            "id": "5",
            "method": "tools/call",
            "params": {"name": "analyze_daily_log_bundle", "arguments": {}},
        },
    )
    resource_response = jsonrpc.post(
        token=codex_token,
        data={
            "jsonrpc": "2.0",
            "id": "6",
            "method": "resources/read",
            "params": {"uri": "skill://workflow/severity_guide"},
        },
    )

    assert tool_response.status_code == 200
    assert tool_response.json()["result"]["isError"] is True
    assert "Unknown tool" in tool_response.json()["result"]["content"][0]["text"]

    assert resource_response.status_code == 200
    assert "error" in resource_response.json()
    assert "Unknown resource" in resource_response.json()["error"]["message"]


def test_api_requires_bearer_token(
    jsonrpc: JsonRpcFixture,
) -> None:
    response = jsonrpc.post(
        token=None,
        data={"jsonrpc": "2.0", "id": "7", "method": "tools/list", "params": {}},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"
