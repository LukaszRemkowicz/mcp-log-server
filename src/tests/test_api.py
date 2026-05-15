from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture

from auth.scopes import (
    CONTAINER_FILES_READ_SCOPE,
    LOGS_COLLECT_SCOPE,
    MCP_HEALTH_READ_SCOPE,
    MCP_STATUS_READ_SCOPE,
    PROJECTS_READ_SCOPE,
    SESSION_CLOSE_SCOPE,
    WORKFLOW_BOOTSTRAP_SCOPE,
    WORKFLOW_SKILLS_READ_SCOPE,
)
from services.docker_service import ContainerPathStat
from tests.conftest import (
    CustomJwtToken,
    FileBackedProjectContext,
    JsonRpcClient,
    MultiProjectCollectContext,
    build_collect_logs_request,
    override_settings,
)
from tools import collection as collection_tools

ToolCall = tuple[str, dict[str, object]]
ProtectedToolCall = tuple[str, dict[str, object], list[str]]
CallerContextToolCall = tuple[str, dict[str, object], list[str], list[str], bool]
ProjectProtectedInvalidTokenFactory = Callable[["CustomJwtToken", list[str]], str]
InvalidTokenFactory = Callable[["CustomJwtToken"], str]


SNAPSHOT_TOOL_CALLS: tuple[ToolCall, ...] = (
    ("list_log_snapshot_files", {"project_name": "landingpage"}),
    (
        "read_log_snapshot_file",
        {"project_name": "landingpage", "source_key": "app_file"},
    ),
    ("grep_log_snapshot", {"project_name": "landingpage", "grep": "line"}),
)
CONTAINER_TOOL_CALLS: tuple[ToolCall, ...] = (
    (
        "read_container_file",
        {"project_name": "dockerpage", "source_key": "backend", "path": "/app/VERSION"},
    ),
    (
        "list_container_directory",
        {"project_name": "dockerpage", "source_key": "backend", "path": "/app"},
    ),
)
ANALYSIS_TOOL_CALLS: tuple[ToolCall, ...] = (
    ("group_errors", {"project_name": "landingpage"}),
    ("build_incident_bundle", {"project_name": "landingpage"}),
    ("create_filtered_view", {"project_name": "landingpage"}),
)
COLLECT_LOGS_TOOL_CALLS: tuple[ToolCall, ...] = (
    (
        "collect_logs",
        {
            "project_names": ["landingpage"],
            "source_keys": ["all"],
            "workspace": "workflow",
        },
    ),
)
PROJECT_PROTECTED_TOOL_CALL_ARGUMENTS: tuple[ToolCall, ...] = (
    COLLECT_LOGS_TOOL_CALLS + SNAPSHOT_TOOL_CALLS + ANALYSIS_TOOL_CALLS + CONTAINER_TOOL_CALLS
)
CALLER_CONTEXT_LOG_TOOL_NAMES = {
    "collect_logs",
    *(tool_name for tool_name, _arguments in SNAPSHOT_TOOL_CALLS + ANALYSIS_TOOL_CALLS),
}
CALLER_CONTEXT_TOOL_CALLS: tuple[CallerContextToolCall, ...] = (
    ("list_projects", {}, [PROJECTS_READ_SCOPE], ["landingpage"], False),
    (
        "collect_logs",
        {"project_names": ["landingpage"], "source_keys": ["app_file"], "workspace": "workflow"},
        [LOGS_COLLECT_SCOPE],
        ["landingpage"],
        False,
    ),
    (
        "list_log_snapshot_files",
        {"project_name": "landingpage"},
        [LOGS_COLLECT_SCOPE],
        ["landingpage"],
        True,
    ),
    (
        "read_log_snapshot_file",
        {"project_name": "landingpage", "source_key": "app_file"},
        [LOGS_COLLECT_SCOPE],
        ["landingpage"],
        True,
    ),
    (
        "grep_log_snapshot",
        {"project_name": "landingpage", "grep": "line"},
        [LOGS_COLLECT_SCOPE],
        ["landingpage"],
        True,
    ),
    ("group_errors", {"project_name": "landingpage"}, [LOGS_COLLECT_SCOPE], ["landingpage"], True),
    (
        "build_incident_bundle",
        {"project_name": "landingpage"},
        [LOGS_COLLECT_SCOPE],
        ["landingpage"],
        True,
    ),
    (
        "create_filtered_view",
        {"project_name": "landingpage"},
        [LOGS_COLLECT_SCOPE],
        ["landingpage"],
        True,
    ),
    (
        "read_container_file",
        {"project_name": "dockerpage", "source_key": "backend", "path": "/app/VERSION"},
        [CONTAINER_FILES_READ_SCOPE],
        ["dockerpage"],
        False,
    ),
    (
        "list_container_directory",
        {"project_name": "dockerpage", "source_key": "backend", "path": "/app"},
        [CONTAINER_FILES_READ_SCOPE],
        ["dockerpage"],
        False,
    ),
)
PROJECT_PROTECTED_LOG_TOOL_CALLS: tuple[ProtectedToolCall, ...] = tuple(
    (tool_name, arguments, [LOGS_COLLECT_SCOPE]) for tool_name, arguments in COLLECT_LOGS_TOOL_CALLS
)
PROJECT_PROTECTED_SNAPSHOT_TOOL_CALLS: tuple[ProtectedToolCall, ...] = tuple(
    (tool_name, arguments, [LOGS_COLLECT_SCOPE]) for tool_name, arguments in SNAPSHOT_TOOL_CALLS
) + tuple(
    (tool_name, arguments, [LOGS_COLLECT_SCOPE]) for tool_name, arguments in ANALYSIS_TOOL_CALLS
)
PROJECT_PROTECTED_CONTAINER_TOOL_CALLS: tuple[ProtectedToolCall, ...] = tuple(
    (tool_name, arguments, [CONTAINER_FILES_READ_SCOPE])
    for tool_name, arguments in CONTAINER_TOOL_CALLS
)
PROJECT_PROTECTED_TOOL_CALLS: tuple[ProtectedToolCall, ...] = (
    PROJECT_PROTECTED_LOG_TOOL_CALLS
    + PROJECT_PROTECTED_SNAPSHOT_TOOL_CALLS
    + PROJECT_PROTECTED_CONTAINER_TOOL_CALLS
)
PROJECT_PROTECTED_SINGLE_PROJECT_TOOL_CALLS: tuple[ProtectedToolCall, ...] = (
    PROJECT_PROTECTED_SNAPSHOT_TOOL_CALLS + PROJECT_PROTECTED_CONTAINER_TOOL_CALLS
)


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
                "group_errors",
                "build_incident_bundle",
                "create_filtered_view",
                "suggest_followup_window",
                "list_projects",
                "get_mcp_service_status",
                "get_mcp_health_check",
            },
            {
                "read_container_file",
                "list_container_directory",
                "close_agent_session",
            },
        ),
        (
            "codex-agent",
            [
                PROJECTS_READ_SCOPE,
                LOGS_COLLECT_SCOPE,
                CONTAINER_FILES_READ_SCOPE,
                SESSION_CLOSE_SCOPE,
                MCP_STATUS_READ_SCOPE,
                MCP_HEALTH_READ_SCOPE,
            ],
            "codex-agent",
            {
                "collect_logs",
                "list_log_snapshot_files",
                "read_log_snapshot_file",
                "grep_log_snapshot",
                "group_errors",
                "build_incident_bundle",
                "create_filtered_view",
                "suggest_followup_window",
                "list_projects",
                "read_container_file",
                "list_container_directory",
                "close_agent_session",
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
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    subject: str,
    scopes: list[str],
    client_id: str,
    expected_present: set[str],
    expected_absent: set[str],
) -> None:
    """Verify JWT scopes hide tools the caller is not allowed to discover."""

    token: str = custom_jwt_token(
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
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify the workflow bootstrap tool returns prompt, skills, and tool inventory."""

    workflow_token: str = custom_jwt_token(
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
    assert all(item["tool_name"] != "group_errors" for item in payload["tools"])
    assert all(item["tool_name"] != "build_incident_bundle" for item in payload["tools"])
    assert all(item["tool_name"] != "create_filtered_view" for item in payload["tools"])
    assert any(item["tool_name"] == "suggest_followup_window" for item in payload["tools"])
    assert any(item["tool_name"] == "list_projects" for item in payload["tools"])
    assert any(item["tool_name"] == "get_mcp_service_status" for item in payload["tools"])
    assert any(item["tool_name"] == "get_mcp_health_check" for item in payload["tools"])
    collect_logs_tool = next(
        item for item in payload["tools"] if item["tool_name"] == "collect_logs"
    )
    assert any(argument["name"] == "project_names" for argument in collect_logs_tool["arguments"])
    assert any(argument["name"] == "source_keys" for argument in collect_logs_tool["arguments"])
    assert any(argument["name"] == "session_id" for argument in collect_logs_tool["arguments"])


def test_service_status_api_does_not_report_project_access(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify service status only reports identity/config, not project permissions."""

    token = custom_jwt_token(
        "codex-agent",
        [MCP_STATUS_READ_SCOPE],
        "status-no-project-client",
        {
            "allowed_projects": ["shop"],
            "client_type": "codex",
            "projects_access": "all",
        },
    )

    response = jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "service-status-caller-context",
            "method": "tools/call",
            "params": {"name": "get_mcp_service_status", "arguments": {}},
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["client_id"] == "status-no-project-client"
    assert payload["client_type"] == "codex"
    assert "allowed_projects" not in payload
    assert "projects_access" not in payload


@pytest.mark.parametrize(
    ("tool_name", "arguments", "scopes", "allowed_projects", "needs_snapshot"),
    CALLER_CONTEXT_TOOL_CALLS,
)
def test_mcp_tools_api_use_database_caller_context_for_project_access(
    file_backed_project_context: FileBackedProjectContext,
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
    tool_name: str,
    arguments: dict[str, object],
    scopes: list[str],
    allowed_projects: list[str],
    needs_snapshot: bool,
) -> None:
    """Verify real MCP tool calls use request-state DB caller projects over JWT claims."""

    client_id = f"caller-context-{allowed_projects[0]}"
    wrong_projects = ["shop"] if allowed_projects != ["shop"] else ["landingpage"]
    token = custom_jwt_token(
        "codex-agent",
        scopes,
        client_id,
        {"allowed_projects": wrong_projects, "client_type": "codex"},
    )

    if tool_name == "read_container_file":
        mocker.patch(
            "tools.container_inspection.docker_service.stat_container_path",
            return_value=ContainerPathStat(
                path="/app/VERSION",
                is_dir=False,
                size=12,
                mode=0o100644,
                modified_at="2026-04-26T10:00:00+00:00",
            ),
        )
        mocker.patch(
            "tools.container_inspection.docker_service.read_container_file",
            return_value=("release-123\n", False),
        )
    if tool_name == "list_container_directory":
        mocker.patch(
            "tools.container_inspection.docker_service.list_container_directory",
            return_value=(
                [
                    ContainerPathStat(
                        path="/app/VERSION",
                        is_dir=False,
                        size=12,
                        mode=0o100644,
                        modified_at="2026-04-26T10:00:00+00:00",
                    ),
                ],
                False,
            ),
        )

    request_data = {
        "jsonrpc": "2.0",
        "id": f"{tool_name}-caller-context",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    if tool_name in CALLER_CONTEXT_LOG_TOOL_NAMES:
        with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
            if needs_snapshot:
                collect_response = jsonrpc.post(
                    token=token,
                    data=build_collect_logs_request(source_keys=["app_file"]),
                )
                assert collect_response.status_code == 200
                assert collect_response.json()["result"]["isError"] is False
            response = jsonrpc.post(token=token, data=request_data)
    else:
        response = jsonrpc.post(token=token, data=request_data)

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    payload = response.json()["result"]["structuredContent"]
    if tool_name == "list_projects":
        assert [project["project_name"] for project in payload["result"]] == allowed_projects
        return
    if tool_name == "collect_logs":
        assert [project["project_name"] for project in payload["projects"]] == allowed_projects
        return
    assert payload["project_name"] == allowed_projects[0]


def test_collect_logs_api_returns_requested_and_resolved_file_sources(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify collect_logs persists requested file sources and reports unknown keys."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        response = jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(
                source_keys=["app_file", "missing_source"],
            ),
        )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert payload["workspace"] == "workflow"
    assert payload["requested_project_names"] == ["landingpage"]
    project_payload = payload["projects"][0]
    assert project_payload["requested_project_name"] == "landingpage"
    assert project_payload["project_name"] == "landingpage"
    assert project_payload["requested_source_keys"] == ["app_file", "missing_source"]
    assert project_payload["requested_since"] == "24h"
    assert project_payload["requested_until"] is None
    assert project_payload["unknown_requested_source_keys"] == ["missing_source"]
    assert project_payload["resolved_source_keys"] == ["app_file"]
    assert project_payload["warnings"] == [
        "Some requested source_keys were not found in the configured manifest: missing_source."
    ]
    assert project_payload["retry_tips"] == [
        "Retry with only source_keys returned by the manifest-backed project configuration."
    ]
    assert project_payload["snapshot_dir"] == str(
        file_backed_project_context.logs_dir / "workflow" / "landingpage" / "latest"
    )
    assert "persisted" not in project_payload
    assert project_payload["sources"][0]["source_key"] == "app_file"
    assert project_payload["sources"][0]["status"] == "collected"
    assert project_payload["sources"][0]["output_file"] == (
        "workflow/landingpage/latest/app_file.log"
    )
    assert project_payload["sources"][0]["line_count"] == 3


@pytest.mark.parametrize(
    ("tool_name", "expected_action"),
    [
        ("group_errors", "group_errors"),
        ("build_incident_bundle", "build_incident_bundle"),
    ],
)
def test_analysis_tools_api_read_collected_snapshot(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
    tool_name: str,
    expected_action: str,
) -> None:
    """Verify grouped analysis tools read the latest collected workflow snapshot."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["app_file"]),
        )
        analysis_response = jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": f"{tool_name}-api",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": {
                        "project_name": "landingpage",
                        "source_keys": ["app_file"],
                        "max_groups": 5,
                    },
                },
            },
        )

    assert collect_response.status_code == 200
    assert collect_response.json()["result"]["isError"] is False
    assert analysis_response.status_code == 200
    assert analysis_response.json()["result"]["isError"] is False

    payload = analysis_response.json()["result"]["structuredContent"]
    assert payload["action"] == expected_action
    assert payload["project_name"] == "landingpage"
    assert payload["workspace"] == "workflow"
    assert payload["snapshot_dir"] == "workflow/landingpage/latest"
    assert payload["searched_source_keys"] == ["app_file"]
    assert payload["grouped_error_count"] == 2
    assert payload["matching_line_count"] == 2

    groups = payload["groups"] if tool_name == "group_errors" else payload["top_groups"]
    assert any(
        group["category"] == "application_error"
        and group["message_summary"] == "Database connection failed"
        and group["first_seen"]["output_file"] == "workflow/landingpage/latest/app_file.log"
        for group in groups
    )


def test_create_filtered_view_api_reads_collected_snapshot(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify create_filtered_view reads a collected snapshot through JSON-RPC."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["app_file"]),
        )
        filtered_response = jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": "create-filtered-view-api",
                "method": "tools/call",
                "params": {
                    "name": "create_filtered_view",
                    "arguments": {
                        "project_name": "landingpage",
                        "source_keys": ["app_file"],
                        "max_lines": 10,
                    },
                },
            },
        )

    assert collect_response.status_code == 200
    assert collect_response.json()["result"]["isError"] is False
    assert filtered_response.status_code == 200
    assert filtered_response.json()["result"]["isError"] is False

    payload = filtered_response.json()["result"]["structuredContent"]
    assert payload["action"] == "create_filtered_view"
    assert payload["project_name"] == "landingpage"
    assert payload["snapshot_dir"] == "workflow/landingpage/latest"
    assert payload["searched_source_keys"] == ["app_file"]
    assert payload["total_line_count"] == 3
    assert payload["kept_line_count"] == 3
    assert payload["excluded_line_count"] == 0
    assert payload["cleaned_lines"][0]["output_file"] == "workflow/landingpage/latest/app_file.log"


@pytest.mark.parametrize(("tool_name", "arguments"), PROJECT_PROTECTED_TOOL_CALL_ARGUMENTS)
def test_project_protected_tools_api_require_bearer_token(
    jsonrpc: JsonRpcClient,
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    """Verify every project-protected tool rejects missing bearer tokens."""

    response = jsonrpc.post(
        token=None,
        data={
            "jsonrpc": "2.0",
            "id": f"{tool_name}-missing-token",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


@pytest.mark.parametrize(("tool_name", "arguments", "scopes"), PROJECT_PROTECTED_TOOL_CALLS)
@pytest.mark.parametrize(
    ("token_factory", "label"),
    [
        (
            lambda custom_jwt_token, scopes: custom_jwt_token(
                "agent",
                scopes,
                "agent",
                {"exp": 1},
            ),
            "expired",
        ),
        (
            lambda custom_jwt_token, scopes: custom_jwt_token(
                "agent",
                scopes,
                "agent",
                {"signing_secret": "wrong-test-secret"},
            ),
            "wrong-secret",
        ),
        (
            lambda custom_jwt_token, scopes: "not-a-jwt",
            "malformed",
        ),
    ],
)
def test_project_protected_tools_api_reject_invalid_bearer_tokens(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    tool_name: str,
    arguments: dict[str, object],
    scopes: list[str],
    token_factory: ProjectProtectedInvalidTokenFactory,
    label: str,
) -> None:
    """Verify project-protected tools reject malformed, expired, or bad tokens."""

    response = jsonrpc.post(
        token=token_factory(custom_jwt_token, scopes),
        data={
            "jsonrpc": "2.0",
            "id": f"{tool_name}-invalid-{label}",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


@pytest.mark.parametrize(
    ("tool_name", "arguments", "scopes"),
    PROJECT_PROTECTED_SINGLE_PROJECT_TOOL_CALLS,
)
def test_project_protected_tools_api_reject_project_access_mismatch(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    tool_name: str,
    arguments: dict[str, object],
    scopes: list[str],
) -> None:
    """Verify single-project tools reject valid tokens for unauthorized projects."""

    token: str = custom_jwt_token("agent", scopes, "agent")
    mismatched_arguments: dict[str, object] = {**arguments, "project_name": "other-project"}

    response = jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": f"{tool_name}-project-mismatch",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": mismatched_arguments},
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert payload["status"] == "error"
    assert payload["error_code"] == "project_access_mismatch"
    assert payload["details"] == {"project_name": "other-project"}


@pytest.mark.parametrize(("tool_name", "arguments"), SNAPSHOT_TOOL_CALLS)
def test_snapshot_tools_api_accept_valid_bearer_token(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    """Verify snapshot tools accept valid tokens after a workflow snapshot exists."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["app_file"]),
        )
        tool_response = jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": f"{tool_name}-valid",
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
        )

    payload = tool_response.json()["result"]["structuredContent"]

    assert collect_response.status_code == 200
    assert collect_response.json()["result"]["isError"] is False
    assert tool_response.status_code == 200
    assert tool_response.json()["result"]["isError"] is False
    assert payload["action"] == tool_name
    assert payload["project_name"] == "landingpage"


def test_list_projects_api_returns_manifest_backed_projects(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify list_projects returns manifest-backed project and source metadata."""

    token: str = custom_jwt_token(
        "workflow-agent",
        [PROJECTS_READ_SCOPE],
        "workflow-agent",
        {"projects_access": "all"},
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
    assert landingpage["project_summary"] == "Landingpage project for analysis tests."
    assert "backend" in landingpage["source_keys"]


def test_read_container_file_api_returns_file_contents(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify read_container_file returns whitelisted container file content."""

    token: str = custom_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["dockerpage"]},
    )

    mocker.patch(
        "tools.container_inspection.docker_service.stat_container_path",
        return_value=ContainerPathStat(
            path="/app/VERSION",
            is_dir=False,
            size=12,
            mode=0o100644,
            modified_at="2026-04-26T10:00:00+00:00",
        ),
    )
    mocker.patch(
        "tools.container_inspection.docker_service.read_container_file",
        return_value=("release-123\n", False),
    )

    response = jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "read-container-file",
            "method": "tools/call",
            "params": {
                "name": "read_container_file",
                "arguments": {
                    "project_name": "dockerpage",
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


def test_list_container_directory_api_returns_entries(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify list_container_directory returns whitelisted container entries."""

    token: str = custom_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["dockerpage"]},
    )

    mocker.patch(
        "tools.container_inspection.docker_service.list_container_directory",
        return_value=(
            [
                ContainerPathStat(
                    path="/app/VERSION",
                    is_dir=False,
                    size=12,
                    mode=0o100644,
                    modified_at="2026-04-26T10:00:00+00:00",
                ),
            ],
            False,
        ),
    )

    response = jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "list-container-directory",
            "method": "tools/call",
            "params": {
                "name": "list_container_directory",
                "arguments": {
                    "project_name": "dockerpage",
                    "source_key": "backend",
                    "path": "/app",
                },
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "list_container_directory"
    assert payload["project_name"] == "dockerpage"
    assert payload["entries"][0]["name"] == "VERSION"


def test_list_projects_api_returns_multiple_manifest_backed_projects(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify list_projects discovers all manifests beside the configured manifest."""

    token: str = custom_jwt_token(
        "workflow-agent",
        [PROJECTS_READ_SCOPE],
        "all-project-workflow-client",
        {"client_type": "workflow_agent", "projects_access": "all"},
    )
    response = jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "list-projects-multi",
            "method": "tools/call",
            "params": {"name": "list_projects", "arguments": {}},
        },
    )

    payload = response.json()["result"]["structuredContent"]["result"]

    assert [item["project_name"] for item in payload] == [
        "alpha",
        "beta",
        "dockerpage",
        "landingpage",
        "other",
        "shop",
    ]
    assert payload[0]["project_summary"] == "Alpha project summary."
    assert payload[1]["project_summary"] == "Beta project summary."
    assert payload[0]["source_keys"] == ["app_file"]
    assert payload[1]["source_keys"] == ["app_file"]


def test_collect_logs_api_returns_agent_error_for_project_mismatch(
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify collect_logs returns a structured project mismatch error."""

    response = jsonrpc.post(
        token=valid_jwt_token,
        data=build_collect_logs_request(project_names=["other-project"]),
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert payload["status"] == "error"
    assert payload["error_code"] == "project_access_mismatch"
    assert payload["retry_tips"] == [
        "Retry with project_names allowed by the current MCP caller project access rules.",
        "Use get_mcp_service_status to confirm the current project access before retrying.",
    ]


@pytest.mark.parametrize("project_names", [None, []])
def test_collect_logs_api_uses_all_accessible_projects_when_project_names_not_provided(
    multi_project_collect_context: MultiProjectCollectContext,
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
    project_names: list[str] | None,
) -> None:
    """Verify omitted or empty project_names expands to all JWT-accessible projects."""

    token: str = custom_jwt_token(
        "workflow-agent",
        [LOGS_COLLECT_SCOPE, PROJECTS_READ_SCOPE],
        "limited-workflow-client",
        {"allowed_projects": ["landingpage", "shop"], "client_type": "workflow_agent"},
    )
    request_data: dict[str, Any] = build_collect_logs_request(project_names=project_names)
    if project_names is None:
        del request_data["params"]["arguments"]["project_names"]
    build_logs_spy = mocker.patch.object(
        collection_tools.collection_service,
        "build_logs",
        wraps=collection_tools.collection_service.build_logs,
    )

    with override_settings(LOGS_DIR=multi_project_collect_context.logs_dir):
        response = jsonrpc.post(
            token=token,
            data=request_data,
        )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["requested_project_names"] == ["landingpage", "shop"]
    assert [item["project_name"] for item in payload["projects"]] == ["landingpage", "shop"]
    assert build_logs_spy.call_count == 2
    assert {call.kwargs["manifest"].project_key for call in build_logs_spy.call_args_list} == {
        "landingpage",
        "shop",
    }
    assert (
        multi_project_collect_context.logs_dir
        / "workflow"
        / "landingpage"
        / "latest"
        / "backend.log"
    ).exists()
    assert (
        multi_project_collect_context.logs_dir / "workflow" / "shop" / "latest" / "app_file.log"
    ).exists()
    assert not (
        multi_project_collect_context.logs_dir / "workflow" / "other" / "latest" / "app_file.log"
    ).exists()


def test_collect_logs_api_generates_session_id_before_tool_call(
    file_backed_project_context: FileBackedProjectContext,
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify MCP middleware injects session_id before collect_logs runs."""

    token = custom_jwt_token(
        "codex-agent",
        [LOGS_COLLECT_SCOPE, PROJECTS_READ_SCOPE],
        "codex-agent",
        {"client_type": "codex"},
    )
    mocker.patch(
        "middleware.audit.agent_call_audit_service.create_tool_call",
        new=mocker.AsyncMock(return_value=uuid4()),
    )
    mocker.patch(
        "middleware.audit.agent_call_audit_service.complete_tool_call",
        new=mocker.AsyncMock(),
    )
    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        response = jsonrpc.post(
            token=token,
            data=build_collect_logs_request(
                source_keys=["app_file"],
                workspace="session",
                session_id=None,
            ),
        )

    payload = response.json()["result"]["structuredContent"]
    session_id = payload["session_id"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert str(UUID(session_id)) == session_id
    assert payload["workspace"] == "session"
    assert (
        file_backed_project_context.logs_dir
        / "sessions"
        / session_id
        / "landingpage"
        / "app_file.log"
    ).exists()


def test_collect_logs_api_blocks_workflow_agent_session_workspace(
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify workflow agent tokens cannot run interactive session collection."""

    create_spy = mocker.patch(
        "middleware.audit.agent_call_audit_service.create_tool_call",
        new=mocker.AsyncMock(),
    )

    response = jsonrpc.post(
        token=valid_jwt_token,
        data=build_collect_logs_request(
            source_keys=["app_file"],
            workspace="session",
            session_id=None,
        ),
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert payload["status"] == "error"
    assert payload["error_code"] == "workspace_not_allowed"
    assert payload["details"] == {
        "client_id": "workflow-agent",
        "client_type": "workflow_agent",
        "workspace": "session",
    }
    create_spy.assert_not_called()


def test_workflow_skill_resource_read_api_returns_skill_contents(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify workflow skill resources return the requested skill text."""

    workflow_token: str = custom_jwt_token(
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
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify resources/list exposes concrete workflow skill resources."""

    workflow_token: str = custom_jwt_token(
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
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify resource templates expose the workflow skill URI template."""

    workflow_token: str = custom_jwt_token(
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
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify unknown workflow skill resources return actionable guidance."""

    workflow_token: str = custom_jwt_token(
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
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify Codex-scoped tokens cannot access workflow-only tools or resources."""

    codex_token: str = custom_jwt_token(
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


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("tools/list", {}),
        ("tools/call", {"name": "list_projects", "arguments": {}}),
        ("resources/read", {"uri": "skill://workflow/severity_guide"}),
    ],
)
def test_api_requires_bearer_token(
    jsonrpc: JsonRpcClient,
    method: str,
    params: dict[str, object],
) -> None:
    """Verify protected MCP methods require a bearer token."""

    response = jsonrpc.post(
        token=None,
        data={"jsonrpc": "2.0", "id": "7", "method": method, "params": params},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


@pytest.mark.parametrize(
    ("token_factory", "label"),
    [
        (
            lambda custom_jwt_token: custom_jwt_token(
                "workflow-agent",
                [LOGS_COLLECT_SCOPE],
                "workflow-agent",
                {"exp": 1},
            ),
            "expired",
        ),
        (
            lambda custom_jwt_token: custom_jwt_token(
                "workflow-agent",
                [LOGS_COLLECT_SCOPE],
                "workflow-agent",
                {"signing_secret": "wrong-test-secret"},
            ),
            "wrong-secret",
        ),
        (
            lambda custom_jwt_token: custom_jwt_token(
                "workflow-agent",
                [LOGS_COLLECT_SCOPE],
                "workflow-agent",
                {"iss": "wrong-issuer"},
            ),
            "wrong-issuer",
        ),
        (
            lambda custom_jwt_token: custom_jwt_token(
                "workflow-agent",
                [LOGS_COLLECT_SCOPE],
                "workflow-agent",
                {"aud": "wrong-audience"},
            ),
            "wrong-audience",
        ),
        (
            lambda custom_jwt_token: "not-a-jwt",
            "malformed",
        ),
    ],
)
def test_api_rejects_invalid_bearer_tokens(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    token_factory: InvalidTokenFactory,
    label: str,
) -> None:
    """Verify protected MCP methods reject invalid bearer tokens."""

    response = jsonrpc.post(
        token=token_factory(custom_jwt_token),
        data={"jsonrpc": "2.0", "id": f"invalid-{label}", "method": "tools/list", "params": {}},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


def test_tool_call_api_rejects_jwt_without_client_id(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify authenticated tool calls require a stable JWT client_id."""

    token = custom_jwt_token(
        "codex-agent",
        [SESSION_CLOSE_SCOPE],
        "codex-agent",
        {"client_id": ""},
    )

    response = jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "missing-client-id",
            "method": "tools/call",
            "params": {
                "name": "close_agent_session",
                "arguments": {"session_id": "ef5e1daa-d06b-479c-926d-8107639bd467"},
            },
        },
    )
    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert payload["status"] == "error"
    assert payload["error_code"] == "invalid_client_id"


def test_tool_call_api_rejects_jwt_for_unregistered_client(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify tool calls require a matching Authentication row."""

    token = custom_jwt_token(
        "unregistered-agent",
        [SESSION_CLOSE_SCOPE],
        "unregistered-agent",
        {"client_type": "codex"},
    )

    response = jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "unregistered-client",
            "method": "tools/call",
            "params": {
                "name": "close_agent_session",
                "arguments": {"session_id": "ef5e1daa-d06b-479c-926d-8107639bd467"},
            },
        },
    )
    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert payload["status"] == "error"
    assert payload["error_code"] == "mcp_client_not_authorized"
    assert payload["details"] == {
        "client_id": "unregistered-agent",
        "client_type": "codex",
        "workspace": "session",
    }
