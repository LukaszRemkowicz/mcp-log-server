from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

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
from core.types import LogWorkspace
from database.models import McpCaller
from services.docker_service import (
    ContainerDetail,
    ContainerDetailEnvVar,
    ContainerDetailMount,
    ContainerDetailNetwork,
    ContainerDetailPort,
    ContainerHealth,
    ContainerPathStat,
    ContainerRestartPolicy,
    VpsContainerInventory,
    VpsVolumeInventory,
)
from services.fail2ban_service import Fail2banActivity, Fail2banJailStatus, Fail2banServiceStatus
from services.tls_certificate_service import TlsCertificateInspection
from tests.conftest import (
    CustomJwtToken,
    FileBackedProjectContext,
    JsonRpcClient,
    MultiProjectCollectContext,
    _seed_project_manifests,
    build_collect_logs_request,
    copy_manifest_and_log_fixtures,
    override_settings,
)
from tools import collection as collection_tools

SESSION_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}-[a-f0-9]{4}$")

ToolCall = tuple[str, dict[str, object]]
ProtectedToolCall = tuple[str, dict[str, object], list[str]]
CallerContextToolCall = tuple[str, dict[str, object], list[str], list[str], bool]
ProjectProtectedInvalidTokenFactory = Callable[["CustomJwtToken", list[str]], str]
InvalidTokenFactory = Callable[["CustomJwtToken"], str]

pytestmark = pytest.mark.anyio


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
        "inspect_project_compose_state",
        {"project_name": "dockerpage"},
    ),
    (
        "inspect_containers_health",
        {"project_name": "dockerpage"},
    ),
    (
        "inspect_container_detail",
        {"project_name": "dockerpage", "source_key": "backend"},
    ),
    (
        "stat_container_path",
        {"project_name": "dockerpage", "source_key": "backend", "path": "/app/VERSION"},
    ),
    (
        "read_container_file",
        {"project_name": "dockerpage", "source_key": "backend", "path": "/app/VERSION"},
    ),
    (
        "list_container_directory",
        {"project_name": "dockerpage", "source_key": "backend", "path": "/app"},
    ),
)
HOST_PATH_TOOL_CALLS: tuple[ToolCall, ...] = (
    ("stat_project_path", {"project_name": "landingpage", "source_key": "app_file"}),
    ("read_project_file", {"project_name": "landingpage", "source_key": "app_file"}),
    ("list_project_directory", {"project_name": "landingpage", "source_key": "app_file"}),
)
FAIL2BAN_TOOL_CALLS: tuple[ToolCall, ...] = (
    ("inspect_live_fail2ban_activity", {"project_name": "landingpage"}),
)
TLS_TOOL_CALLS: tuple[ToolCall, ...] = (("inspect_tls_certificate", {}),)
PROJECT_MANIFEST_TOOL_CALLS: tuple[ToolCall, ...] = (
    ("read_project_manifest", {"project_name": "landingpage"}),
)
ANALYSIS_TOOL_CALLS: tuple[ToolCall, ...] = (
    ("group_errors", {"project_name": "landingpage"}),
    ("build_incident_bundle", {"project_name": "landingpage"}),
    ("create_filtered_view", {"project_name": "landingpage"}),
    ("inspect_proxy_activity", {"project_name": "landingpage"}),
)
COLLECT_LOGS_TOOL_CALLS: tuple[ToolCall, ...] = (
    (
        "collect_logs",
        {
            "project_names": ["landingpage"],
            "source_keys": ["all"],
        },
    ),
)
PROJECT_PROTECTED_TOOL_CALL_ARGUMENTS: tuple[ToolCall, ...] = (
    COLLECT_LOGS_TOOL_CALLS
    + PROJECT_MANIFEST_TOOL_CALLS
    + SNAPSHOT_TOOL_CALLS
    + ANALYSIS_TOOL_CALLS
    + CONTAINER_TOOL_CALLS
    + HOST_PATH_TOOL_CALLS
    + FAIL2BAN_TOOL_CALLS
)
CALLER_CONTEXT_LOG_TOOL_NAMES = {
    "collect_logs",
    *(tool_name for tool_name, _arguments in SNAPSHOT_TOOL_CALLS + ANALYSIS_TOOL_CALLS),
}
CALLER_CONTEXT_TOOL_CALLS: tuple[CallerContextToolCall, ...] = (
    ("list_projects", {}, [PROJECTS_READ_SCOPE], ["landingpage"], False),
    (
        "read_project_manifest",
        {"project_name": "landingpage"},
        [PROJECTS_READ_SCOPE],
        ["landingpage"],
        False,
    ),
    (
        "collect_logs",
        {"project_names": ["landingpage"], "source_keys": ["app_file"]},
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
        "inspect_containers_health",
        {"project_name": "dockerpage"},
        [CONTAINER_FILES_READ_SCOPE],
        ["dockerpage"],
        False,
    ),
    (
        "inspect_container_detail",
        {"project_name": "dockerpage", "source_key": "backend"},
        [CONTAINER_FILES_READ_SCOPE],
        ["dockerpage"],
        False,
    ),
    (
        "stat_container_path",
        {"project_name": "dockerpage", "source_key": "backend", "path": "/app/VERSION"},
        [CONTAINER_FILES_READ_SCOPE],
        ["dockerpage"],
        False,
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
    (
        "stat_project_path",
        {"project_name": "landingpage", "source_key": "app_file"},
        [CONTAINER_FILES_READ_SCOPE],
        ["landingpage"],
        False,
    ),
    (
        "read_project_file",
        {"project_name": "landingpage", "source_key": "app_file"},
        [CONTAINER_FILES_READ_SCOPE],
        ["landingpage"],
        False,
    ),
    (
        "list_project_directory",
        {"project_name": "landingpage", "source_key": "app_file"},
        [CONTAINER_FILES_READ_SCOPE],
        ["landingpage"],
        False,
    ),
)
PROJECT_PROTECTED_LOG_TOOL_CALLS: tuple[ProtectedToolCall, ...] = tuple(
    (tool_name, arguments, [LOGS_COLLECT_SCOPE]) for tool_name, arguments in COLLECT_LOGS_TOOL_CALLS
)
PROJECT_PROTECTED_MANIFEST_TOOL_CALLS: tuple[ProtectedToolCall, ...] = tuple(
    (tool_name, arguments, [PROJECTS_READ_SCOPE])
    for tool_name, arguments in PROJECT_MANIFEST_TOOL_CALLS
)
PROJECT_PROTECTED_SNAPSHOT_TOOL_CALLS: tuple[ProtectedToolCall, ...] = tuple(
    (tool_name, arguments, [LOGS_COLLECT_SCOPE]) for tool_name, arguments in SNAPSHOT_TOOL_CALLS
) + tuple(
    (tool_name, arguments, [LOGS_COLLECT_SCOPE]) for tool_name, arguments in ANALYSIS_TOOL_CALLS
)
PROJECT_PROTECTED_CONTAINER_TOOL_CALLS: tuple[ProtectedToolCall, ...] = tuple(
    (tool_name, arguments, [CONTAINER_FILES_READ_SCOPE])
    for tool_name, arguments in CONTAINER_TOOL_CALLS + HOST_PATH_TOOL_CALLS
)
PROJECT_PROTECTED_FAIL2BAN_TOOL_CALLS: tuple[ProtectedToolCall, ...] = tuple(
    (tool_name, arguments, [MCP_STATUS_READ_SCOPE]) for tool_name, arguments in FAIL2BAN_TOOL_CALLS
)
PROJECT_PROTECTED_TOOL_CALLS: tuple[ProtectedToolCall, ...] = (
    PROJECT_PROTECTED_LOG_TOOL_CALLS
    + PROJECT_PROTECTED_MANIFEST_TOOL_CALLS
    + PROJECT_PROTECTED_SNAPSHOT_TOOL_CALLS
    + PROJECT_PROTECTED_CONTAINER_TOOL_CALLS
    + PROJECT_PROTECTED_FAIL2BAN_TOOL_CALLS
)
PROJECT_PROTECTED_SINGLE_PROJECT_TOOL_CALLS: tuple[ProtectedToolCall, ...] = (
    PROJECT_PROTECTED_MANIFEST_TOOL_CALLS
    + PROJECT_PROTECTED_SNAPSHOT_TOOL_CALLS
    + PROJECT_PROTECTED_CONTAINER_TOOL_CALLS
    + PROJECT_PROTECTED_FAIL2BAN_TOOL_CALLS
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
                "inspect_proxy_activity",
                "suggest_followup_window",
                "inspect_tls_certificate",
                "list_projects",
                "read_project_manifest",
                "get_mcp_service_status",
                "get_mcp_health_check",
            },
            {
                "inspect_containers_health",
                "inspect_project_compose_state",
                "inspect_vps_containers",
                "inspect_vps_volumes",
                "inspect_container_detail",
                "stat_container_path",
                "read_container_file",
                "list_container_directory",
                "stat_project_path",
                "read_project_file",
                "list_project_directory",
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
                "inspect_proxy_activity",
                "suggest_followup_window",
                "inspect_tls_certificate",
                "list_projects",
                "read_project_manifest",
                "inspect_containers_health",
                "inspect_project_compose_state",
                "inspect_vps_containers",
                "inspect_vps_volumes",
                "inspect_container_detail",
                "stat_container_path",
                "read_container_file",
                "list_container_directory",
                "stat_project_path",
                "read_project_file",
                "list_project_directory",
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
async def test_tools_list_filters_visible_tools_per_jwt(
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

    response = await jsonrpc.post(
        token=token,
        data={"jsonrpc": "2.0", "id": "2", "method": "tools/list", "params": {}},
    )

    tool_names = {tool["name"] for tool in response.json()["result"]["tools"]}

    assert expected_present <= tool_names
    assert tool_names.isdisjoint(expected_absent)


async def test_analyze_daily_log_bundle_api_returns_structured_workflow_bootstrap(
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

    response = await jsonrpc.post(
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
    assert isinstance(payload["prompt"], str)
    assert payload["prompt"]
    assert "return `action=read_skills` for" in payload["prompt"]
    assert "`bot_detection` before `final_report`" in payload["prompt"]
    assert "instead of relying on model memory" in payload["prompt"]
    assert "possible security impact" in payload["prompt"]
    assert "successful sensitive-path access" in payload["prompt"]
    assert "`owasp_security` before `final_report`" in payload["prompt"]
    assert any(
        item["resource_uri"] == "skill://workflow/severity_guide"
        for item in payload["mandatory_skills"]
    )
    assert any(
        item["resource_uri"] == "skill://workflow/normal_patterns"
        for item in payload["mandatory_skills"]
    )
    assert any(
        item["resource_uri"] == "skill://workflow/application_monitoring"
        for item in payload["mandatory_skills"]
    )
    assert any(
        item["resource_uri"] == "skill://workflow/bot_detection"
        for item in payload["optional_skills"]
    )
    bot_detection = next(
        item
        for item in payload["optional_skills"]
        if item["resource_uri"] == "skill://workflow/bot_detection"
    )
    assert "scanner/probe-heavy traffic" in bot_detection["when_useful"]
    assert any(item["tool_name"] == "collect_logs" for item in payload["tools"])
    assert any(item["tool_name"] == "list_log_snapshot_files" for item in payload["tools"])
    assert any(item["tool_name"] == "read_log_snapshot_file" for item in payload["tools"])
    assert any(item["tool_name"] == "grep_log_snapshot" for item in payload["tools"])
    assert any(item["tool_name"] == "group_errors" for item in payload["tools"])
    assert any(item["tool_name"] == "build_incident_bundle" for item in payload["tools"])
    assert any(item["tool_name"] == "create_filtered_view" for item in payload["tools"])
    assert any(item["tool_name"] == "inspect_proxy_activity" for item in payload["tools"])
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
    assert all(argument["name"] != "workspace" for argument in collect_logs_tool["arguments"])


async def test_service_status_api_does_not_report_project_access(
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

    response = await jsonrpc.post(
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
async def test_mcp_tools_api_use_database_caller_context_for_project_access(
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

    if tool_name in {"read_container_file", "stat_container_path"}:
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
    if tool_name == "inspect_containers_health":
        mocker.patch(
            "tools.container_inspection.docker_service.inspect_container_health",
            return_value=ContainerHealth(
                container_id="abc123def456",
                container_name="backend-container",
                image="portfolio/backend:2026-05-16",
                docker_status="running",
                health_status="healthy",
                running=True,
                restarting=False,
                paused=False,
                dead=False,
                exit_code=0,
                error="",
                restart_count=2,
                started_at="2026-05-16T10:00:00.000000000Z",
                finished_at="0001-01-01T00:00:00Z",
            ),
        )
    if tool_name == "inspect_container_detail":
        mocker.patch(
            "tools.container_inspection.docker_service.inspect_container_detail",
            return_value=ContainerDetail(
                health=ContainerHealth(
                    container_id="abc123def456",
                    container_name="app-container",
                    image="portfolio/backend:2026-05-16",
                    docker_status="running",
                    health_status="healthy",
                    running=True,
                    restarting=False,
                    paused=False,
                    dead=False,
                    exit_code=0,
                    error="",
                    restart_count=2,
                    started_at="2026-05-16T10:00:00.000000000Z",
                    finished_at=None,
                ),
                created_at="2026-05-16T09:55:00.000000000Z",
                env_var_names=[],
                label_keys=[],
                compose_labels={},
                restart_policy=ContainerRestartPolicy(
                    name=None,
                    maximum_retry_count=None,
                ),
                command=[],
                entrypoint=[],
                working_dir=None,
                user=None,
                ports=[],
                mounts=[],
                networks=[],
                health_log=[],
            ),
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
    if tool_name == "inspect_project_compose_state":
        mocker.patch(
            "tools.container_inspection.docker_service.inspect_vps_containers",
            return_value=[
                VpsContainerInventory(
                    container_id="abc123def4567890",
                    short_container_id="abc123def456",
                    container_name="app-container",
                    image="portfolio/backend:2026-05-16",
                    command=[],
                    command_preview="",
                    created_at=None,
                    docker_status="running",
                    state="running",
                    health_status="healthy",
                    running=True,
                    restarting=False,
                    paused=False,
                    dead=False,
                    exit_code=0,
                    error="",
                    restart_count=0,
                    started_at=None,
                    finished_at=None,
                    compose_labels={
                        "com.docker.compose.project": "dockerpage",
                        "com.docker.compose.service": "backend",
                    },
                    restart_policy=ContainerRestartPolicy(
                        name="unless-stopped",
                        maximum_retry_count=0,
                    ),
                    ports=[],
                    network_names=[],
                    triage_notes=[],
                )
            ],
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
                collect_response = await jsonrpc.post(
                    token=token,
                    data=build_collect_logs_request(source_keys=["app_file"]),
                )
                assert collect_response.status_code == 200
                assert collect_response.json()["result"]["isError"] is False
            response = await jsonrpc.post(token=token, data=request_data)
    else:
        response = await jsonrpc.post(token=token, data=request_data)

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


async def test_collect_logs_api_returns_requested_and_resolved_file_sources(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify collect_logs persists requested file sources and reports unknown keys."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        response = await jsonrpc.post(
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


async def test_collect_logs_api_errors_when_all_requested_sources_are_unknown(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify collect_logs does not look successful when no source can be collected."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        response = await jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["missing_source"]),
        )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert payload["status"] == "error"
    assert payload["error_code"] == "unknown_source_keys"
    assert payload["message"] == "No requested source_keys were found in the configured manifest."
    assert payload["details"] == {
        "project_name": "landingpage",
        "requested_source_keys": ["missing_source"],
        "unknown_requested_source_keys": ["missing_source"],
    }


async def test_snapshot_tools_api_support_single_source_alias_and_match_windows(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify snapshot grep/read edge cases through the real JSON-RPC path."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = await jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["snapshot_text"]),
        )
        grep_response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": "grep-source-key-window",
                "method": "tools/call",
                "params": {
                    "name": "grep_log_snapshot",
                    "arguments": {
                        "project_name": "landingpage",
                        "source_key": "snapshot_text",
                        "grep": "match",
                        "match_offset": 1,
                        "max_matches": 2,
                    },
                },
            },
        )
        read_response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": "read-line-window",
                "method": "tools/call",
                "params": {
                    "name": "read_log_snapshot_file",
                    "arguments": {
                        "project_name": "landingpage",
                        "source_key": "snapshot_text",
                        "start_line": 2,
                        "line_count": 2,
                        "max_bytes": 100,
                    },
                },
            },
        )

    grep_payload = grep_response.json()["result"]["structuredContent"]
    read_payload = read_response.json()["result"]["structuredContent"]

    assert collect_response.status_code == 200
    assert collect_response.json()["result"]["isError"] is False
    assert grep_response.status_code == 200
    assert grep_response.json()["result"]["isError"] is False
    assert grep_payload["searched_source_keys"] == ["snapshot_text"]
    assert grep_payload["matched_source_keys"] == ["snapshot_text"]
    assert grep_payload["match_offset"] == 1
    assert grep_payload["max_matches"] == 2
    assert grep_payload["match_count"] == 4
    assert grep_payload["returned_match_count"] == 2
    assert grep_payload["truncated"] is True
    assert [match["line"] for match in grep_payload["matches"]] == ["match two", "match three"]
    assert [match["line_number"] for match in grep_payload["matches"]] == [3, 4]
    assert all(match["line_truncated"] is False for match in grep_payload["matches"])
    assert read_response.status_code == 200
    assert read_response.json()["result"]["isError"] is False
    assert read_payload["source_key"] == "snapshot_text"
    assert read_payload["start_line"] == 2
    assert read_payload["line_count"] == 2
    assert read_payload["content"] == "match one\nmatch two\n"
    assert read_payload["output_file"] == "workflow/landingpage/latest/snapshot_text.log"
    assert read_payload["returned_bytes"] == len(b"match one\nmatch two\n")
    assert read_payload["truncated"] is False


async def test_grep_log_snapshot_api_accepts_max_matches(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify agents can use max_matches to cap grep results."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = await jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["snapshot_text"]),
        )
        grep_response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": "grep-max-matches",
                "method": "tools/call",
                "params": {
                    "name": "grep_log_snapshot",
                    "arguments": {
                        "project_name": "landingpage",
                        "source_key": "snapshot_text",
                        "grep": "match",
                        "max_matches": 2,
                    },
                },
            },
        )

    grep_payload = grep_response.json()["result"]["structuredContent"]

    assert collect_response.status_code == 200
    assert collect_response.json()["result"]["isError"] is False
    assert grep_response.status_code == 200
    assert grep_response.json()["result"]["isError"] is False
    assert grep_payload["max_matches"] == 2
    assert grep_payload["match_count"] == 4
    assert grep_payload["returned_match_count"] == 2
    assert grep_payload["truncated"] is True
    assert [match["line"] for match in grep_payload["matches"]] == ["match one", "match two"]


async def test_snapshot_tools_api_support_all_source_keys_alias(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify snapshot and analysis follow-up tools accept source_keys=["all"]."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = await jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["all"]),
        )
        list_response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": "list-all-source-keys",
                "method": "tools/call",
                "params": {
                    "name": "list_log_snapshot_files",
                    "arguments": {"project_name": "landingpage"},
                },
            },
        )
        grep_response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": "grep-all-source-keys",
                "method": "tools/call",
                "params": {
                    "name": "grep_log_snapshot",
                    "arguments": {
                        "project_name": "landingpage",
                        "source_keys": ["all"],
                        "grep": "Database connection failed",
                    },
                },
            },
        )
        filtered_response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": "filtered-all-source-keys",
                "method": "tools/call",
                "params": {
                    "name": "create_filtered_view",
                    "arguments": {
                        "project_name": "landingpage",
                        "source_keys": ["all"],
                        "max_lines": 10,
                    },
                },
            },
        )
        group_response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": "group-all-source-keys",
                "method": "tools/call",
                "params": {
                    "name": "group_errors",
                    "arguments": {
                        "project_name": "landingpage",
                        "source_keys": ["all"],
                        "max_groups": 5,
                    },
                },
            },
        )
        bundle_response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": "bundle-all-source-keys",
                "method": "tools/call",
                "params": {
                    "name": "build_incident_bundle",
                    "arguments": {
                        "project_name": "landingpage",
                        "source_keys": ["all"],
                        "max_groups": 5,
                        "max_lines_per_source": 5,
                    },
                },
            },
        )

    expected_source_keys = [
        "backend",
        "nginx",
        "app_file",
        "app_first",
        "app_second",
        "snapshot_text",
        "traefik",
    ]
    collect_payload = collect_response.json()["result"]["structuredContent"]["projects"][0]
    list_payload = list_response.json()["result"]["structuredContent"]
    grep_payload = grep_response.json()["result"]["structuredContent"]
    filtered_payload = filtered_response.json()["result"]["structuredContent"]
    group_payload = group_response.json()["result"]["structuredContent"]
    bundle_payload = bundle_response.json()["result"]["structuredContent"]

    assert collect_response.status_code == 200
    assert collect_response.json()["result"]["isError"] is False
    assert collect_payload["resolved_source_keys"] == expected_source_keys
    assert list_response.status_code == 200
    assert list_response.json()["result"]["isError"] is False
    assert list_payload["workspace"] == "workflow"
    assert (
        list_payload["session_id"]
        == collect_response.json()["result"]["structuredContent"]["session_id"]
    )
    assert [item["source_key"] for item in list_payload["files"]] == expected_source_keys
    assert grep_response.status_code == 200
    assert grep_response.json()["result"]["isError"] is False
    assert grep_payload["searched_source_keys"] == expected_source_keys
    assert grep_payload["matched_source_keys"] == ["app_file", "backend"]
    assert filtered_response.status_code == 200
    assert filtered_response.json()["result"]["isError"] is False
    assert filtered_payload["searched_source_keys"] == expected_source_keys
    assert group_response.status_code == 200
    assert group_response.json()["result"]["isError"] is False
    assert group_payload["searched_source_keys"] == expected_source_keys
    assert bundle_response.status_code == 200
    assert bundle_response.json()["result"]["isError"] is False
    assert bundle_payload["searched_source_keys"] == expected_source_keys


async def test_grep_log_snapshot_api_truncates_large_matching_lines(
    tmp_path: Path,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify grep line truncation is preserved through FastMCP serialization."""

    fixture_root = copy_manifest_and_log_fixtures(tmp_path)
    long_line = "match " + ("x" * 4_000)
    (fixture_root / "logs" / "landingpage" / "app_file.log").write_text(
        f"{long_line}\n",
        encoding="utf-8",
    )
    await _seed_project_manifests(fixture_root / "manifests")
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        collect_response = await jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["app_file"]),
        )
        grep_response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": "grep-long-line",
                "method": "tools/call",
                "params": {
                    "name": "grep_log_snapshot",
                    "arguments": {
                        "project_name": "landingpage",
                        "source_key": "app_file",
                        "grep": "match",
                    },
                },
            },
        )

    payload = grep_response.json()["result"]["structuredContent"]
    match = payload["matches"][0]

    assert collect_response.status_code == 200
    assert collect_response.json()["result"]["isError"] is False
    assert grep_response.status_code == 200
    assert grep_response.json()["result"]["isError"] is False
    assert payload["match_count"] == 1
    assert match["line_truncated"] is True
    assert len(match["line"].encode("utf-8")) == 2_000
    assert match["line"] == long_line[:2_000]


async def test_snapshot_tools_api_reject_conflicting_source_key_arguments(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify source_key/source_keys conflicts are rejected at the MCP boundary."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = await jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["snapshot_text"]),
        )
        response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": "grep-conflicting-source-keys",
                "method": "tools/call",
                "params": {
                    "name": "grep_log_snapshot",
                    "arguments": {
                        "project_name": "landingpage",
                        "source_key": "snapshot_text",
                        "source_keys": ["snapshot_text"],
                        "grep": "match",
                    },
                },
            },
        )

    payload = response.json()["result"]["structuredContent"]

    assert collect_response.status_code == 200
    assert collect_response.json()["result"]["isError"] is False
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert payload["status"] == "error"
    assert payload["error_code"] == "invalid_source_key_arguments"
    assert payload["details"] == {
        "source_key": "snapshot_text",
        "source_keys": ["snapshot_text"],
    }


@pytest.mark.parametrize(
    ("tool_name", "expected_action"),
    [
        ("group_errors", "group_errors"),
        ("build_incident_bundle", "build_incident_bundle"),
    ],
)
async def test_analysis_tools_api_read_collected_snapshot(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
    tool_name: str,
    expected_action: str,
) -> None:
    """Verify grouped analysis tools read the latest collected workflow snapshot."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = await jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["app_file"]),
        )
        analysis_response = await jsonrpc.post(
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
    if tool_name == "group_errors":
        assert payload["summary"].startswith("Found 2 error-like lines in 2 groups.")
        assert "Database connection failed" in payload["summary"]
        assert len(payload["summary"]) < 260


async def test_inspect_proxy_activity_api_groups_proxy_status_signals(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify proxy diagnostics summarize collected ingress/proxy snapshot sources."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = await jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["nginx", "traefik"]),
        )
        response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": "inspect-proxy-activity",
                "method": "tools/call",
                "params": {
                    "name": "inspect_proxy_activity",
                    "arguments": {
                        "project_name": "landingpage",
                        "source_keys": ["all"],
                        "max_groups": 10,
                    },
                },
            },
        )

    payload = response.json()["result"]["structuredContent"]

    assert collect_response.status_code == 200
    assert collect_response.json()["result"]["isError"] is False
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "inspect_proxy_activity"
    assert payload["project_name"] == "landingpage"
    assert payload["workspace"] == "workflow"
    assert payload["snapshot_dir"] == "workflow/landingpage/latest"
    assert payload["searched_source_keys"] == ["nginx", "traefik"]
    assert payload["total_line_count"] == 8
    assert payload["parsed_proxy_line_count"] == 8
    assert payload["http_status_line_count"] == 6
    assert payload["upstream_error_count"] == 1
    assert payload["truncated"] is False
    assert payload["returned_route_group_count"] == 5
    assert payload["distinct_route_group_count"] == 5
    assert payload["distinct_route_group_count_is_exact"] is True
    assert payload["omitted_route_group_count"] == 0
    assert payload["route_groups_omitted"] is False
    assert payload["status_class_counts"] == [
        {"status_class": "2xx", "count": 1},
        {"status_class": "3xx", "count": 2},
        {"status_class": "4xx", "count": 2},
        {"status_class": "5xx", "count": 1},
    ]
    assert payload["top_routes"][0]["path"] == "/admin"
    assert payload["top_routes"][0]["status_code"] == 404
    assert payload["top_routes"][0]["count"] == 2
    assert payload["top_routes"][1]["path"] == "/api/orders"
    assert payload["top_routes"][1]["status_code"] == 502
    assert payload["top_routes"][1]["is_upstream_error"] is True


async def test_vps_security_fixture_logs_support_snapshot_analysis(
    file_backed_project_context: FileBackedProjectContext,
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify vps-security fixture logs exercise fail2ban and proxy analysis tools."""

    token = custom_jwt_token(
        "all-project-workflow-client",
        [LOGS_COLLECT_SCOPE],
        "all-project-workflow-client",
        {"client_type": "workflow_agent"},
    )

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = await jsonrpc.post(
            token=token,
            data=build_collect_logs_request(
                request_id="collect-vps-security-fixture",
                project_names=["vps-security"],
                source_keys=["all"],
                since="30d",
            ),
        )
        grep_response = await jsonrpc.post(
            token=token,
            data={
                "jsonrpc": "2.0",
                "id": "grep-vps-security-regex-or-fixture",
                "method": "tools/call",
                "params": {
                    "name": "grep_log_snapshot",
                    "arguments": {
                        "project_name": "vps-security",
                        "grep": "Ban|wp-login|502",
                        "source_keys": ["fail2ban", "nginx_access", "traefik_access"],
                    },
                },
            },
        )
        proxy_response = await jsonrpc.post(
            token=token,
            data={
                "jsonrpc": "2.0",
                "id": "inspect-vps-security-proxy-fixture",
                "method": "tools/call",
                "params": {
                    "name": "inspect_proxy_activity",
                    "arguments": {
                        "project_name": "vps-security",
                        "source_keys": ["nginx_access", "traefik_access"],
                        "max_groups": 30,
                    },
                },
            },
        )
        group_response = await jsonrpc.post(
            token=token,
            data={
                "jsonrpc": "2.0",
                "id": "group-vps-security-fixture",
                "method": "tools/call",
                "params": {
                    "name": "group_errors",
                    "arguments": {
                        "project_name": "vps-security",
                        "source_keys": ["fail2ban", "nginx_access", "traefik_access"],
                        "max_groups": 6,
                    },
                },
            },
        )

    collect_payload = collect_response.json()["result"]["structuredContent"]["projects"][0]
    grep_payload = grep_response.json()["result"]["structuredContent"]
    proxy_payload = proxy_response.json()["result"]["structuredContent"]
    group_payload = group_response.json()["result"]["structuredContent"]

    assert collect_response.status_code == 200
    assert collect_response.json()["result"]["isError"] is False
    assert collect_payload["resolved_source_keys"] == [
        "fail2ban",
        "nginx_access",
        "nginx_runtime",
        "traefik_access",
    ]
    assert [source["line_count"] for source in collect_payload["sources"]] == [20, 12, 1, 12]
    assert grep_response.status_code == 200
    assert grep_response.json()["result"]["isError"] is False
    assert grep_payload["match_count"] == 8
    assert grep_payload["matched_source_keys"] == [
        "fail2ban",
        "nginx_access",
        "traefik_access",
    ]
    assert proxy_response.status_code == 200
    assert proxy_response.json()["result"]["isError"] is False
    assert proxy_payload["total_line_count"] == 24
    assert proxy_payload["parsed_proxy_line_count"] == 24
    assert proxy_payload["http_status_line_count"] == 24
    assert proxy_payload["upstream_error_count"] == 3
    assert proxy_payload["status_class_counts"] == [
        {"status_class": "2xx", "count": 5},
        {"status_class": "3xx", "count": 1},
        {"status_class": "4xx", "count": 15},
        {"status_class": "5xx", "count": 3},
    ]
    assert any(
        route["path"] == "/travel"
        and route["status_code"] == 504
        and route["is_upstream_error"] is True
        for route in proxy_payload["top_routes"]
    )
    assert group_response.status_code == 200
    assert group_response.json()["result"]["isError"] is False
    assert group_payload["matching_line_count"] == 19


async def test_inspect_probe_blocking_activity_api_correlates_probe_and_fail2ban_events(
    file_backed_project_context: FileBackedProjectContext,
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify probe-blocking diagnostics correlate proxy probes with fail2ban events."""

    token = custom_jwt_token(
        "all-project-workflow-client",
        [LOGS_COLLECT_SCOPE],
        "all-project-workflow-client",
        {"client_type": "workflow_agent"},
    )

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = await jsonrpc.post(
            token=token,
            data=build_collect_logs_request(
                request_id="collect-vps-security-probe-blocking",
                project_names=["vps-security"],
                source_keys=["all"],
                since="30d",
            ),
        )
        response = await jsonrpc.post(
            token=token,
            data={
                "jsonrpc": "2.0",
                "id": "inspect-probe-blocking-activity",
                "method": "tools/call",
                "params": {
                    "name": "inspect_probe_blocking_activity",
                    "arguments": {
                        "project_name": "vps-security",
                        "source_keys": ["fail2ban", "nginx_access", "traefik_access"],
                    },
                },
            },
        )

    payload = response.json()["result"]["structuredContent"]

    assert collect_response.status_code == 200
    assert collect_response.json()["result"]["isError"] is False
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "inspect_probe_blocking_activity"
    assert payload["project_name"] == "vps-security"
    assert payload["searched_source_keys"] == ["fail2ban", "nginx_access", "traefik_access"]
    assert payload["policy"] == {
        "portfolio-nginx-probes": {"findtime": "1m", "maxretry": 3, "bantime": "-1"},
        "portfolio-traefik-probes": {"findtime": "1m", "maxretry": 3, "bantime": "-1"},
    }
    assert payload["suspicious_ip_count"] == 4
    assert payload["suspicious_request_count"] == 8
    assert payload["expected_ban_ip_count"] == 1
    assert payload["observed_ban_ip_count"] == 3
    assert payload["expected_but_not_observed"] == []

    nginx_record = next(
        item
        for item in payload["suspicious_ips"]
        if item["ip"] == "203.0.113.10" and item["jail"] == "portfolio-nginx-probes"
    )
    assert nginx_record["request_count"] == 4
    assert nginx_record["paths"] == ["/.env", "/.git/config", "/phpmyadmin/index.php", "/wp-admin"]
    assert nginx_record["expected_ban"] is True
    assert nginx_record["observed_ban"] is True
    assert nginx_record["ban_count"] == 1
    assert nginx_record["already_banned_count"] == 1
    assert nginx_record["last_ban_at"] == "2026-05-18 09:40:01"

    unobserved_record = next(
        item
        for item in payload["suspicious_ips"]
        if item["ip"] == "198.51.100.99" and item["jail"] == "portfolio-traefik-probes"
    )
    assert unobserved_record["expected_ban"] is False
    assert unobserved_record["observed_ban"] is False


@pytest.mark.parametrize(
    "tool_name",
    [
        "group_errors",
        "build_incident_bundle",
        "create_filtered_view",
        "inspect_proxy_activity",
        "inspect_probe_blocking_activity",
    ],
)
async def test_analysis_tools_api_support_single_source_alias(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
    tool_name: str,
) -> None:
    """Verify analysis tools accept source_key as the single-source alias."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = await jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["app_file"]),
        )
        response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": f"{tool_name}-source-key-alias",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": {
                        "project_name": "landingpage",
                        "source_key": "app_file",
                    },
                },
            },
        )

    payload = response.json()["result"]["structuredContent"]

    assert collect_response.status_code == 200
    assert collect_response.json()["result"]["isError"] is False
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["searched_source_keys"] == ["app_file"]


@pytest.mark.parametrize(
    "tool_name",
    [
        "group_errors",
        "build_incident_bundle",
        "create_filtered_view",
        "inspect_proxy_activity",
        "inspect_probe_blocking_activity",
    ],
)
async def test_analysis_tools_api_reject_conflicting_source_key_arguments(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
    tool_name: str,
) -> None:
    """Verify analysis tools reject source_key and source_keys together."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = await jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["app_file"]),
        )
        response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": f"{tool_name}-conflicting-source-keys",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": {
                        "project_name": "landingpage",
                        "source_key": "app_file",
                        "source_keys": ["app_file"],
                    },
                },
            },
        )

    payload = response.json()["result"]["structuredContent"]

    assert collect_response.status_code == 200
    assert collect_response.json()["result"]["isError"] is False
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert payload["status"] == "error"
    assert payload["error_code"] == "invalid_source_key_arguments"
    assert payload["details"] == {
        "source_key": "app_file",
        "source_keys": ["app_file"],
    }


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("group_errors", {"max_groups": 0}),
        ("build_incident_bundle", {"max_groups": 0}),
        ("create_filtered_view", {"max_lines": 0}),
        ("inspect_proxy_activity", {"max_groups": 0}),
    ],
)
async def test_analysis_tools_api_validate_requested_snapshot_before_tool_arguments(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    """Verify snapshot lookup runs before analysis-specific argument validation."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": f"{tool_name}-snapshot-first",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": {
                        "project_name": "landingpage",
                        **arguments,
                    },
                },
            },
        )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert payload["error_code"] == "snapshot_not_found"
    assert payload["message"] == "Requested workflow log snapshot was not found."


async def test_create_filtered_view_api_reads_collected_snapshot(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify create_filtered_view reads a collected snapshot through JSON-RPC."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = await jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["app_file"]),
        )
        filtered_response = await jsonrpc.post(
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
                        "view_mode": "head",
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
    assert payload["view_mode"] == "head"
    assert payload["total_line_count"] == 3
    assert payload["kept_line_count"] == 3
    assert payload["excluded_line_count"] == 0
    assert payload["cleaned_lines"][0]["output_file"] == "workflow/landingpage/latest/app_file.log"


async def test_create_filtered_view_api_rejects_unknown_view_mode(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify create_filtered_view returns agent-facing mode validation errors."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = await jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["app_file"]),
        )
        filtered_response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": "create-filtered-view-invalid-mode-api",
                "method": "tools/call",
                "params": {
                    "name": "create_filtered_view",
                    "arguments": {
                        "project_name": "landingpage",
                        "source_keys": ["app_file"],
                        "view_mode": "latest",
                    },
                },
            },
        )

    assert collect_response.status_code == 200
    assert collect_response.json()["result"]["isError"] is False
    assert filtered_response.status_code == 200
    assert filtered_response.json()["result"]["isError"] is True

    payload = filtered_response.json()["result"]["structuredContent"]
    assert payload["status"] == "error"
    assert payload["error_code"] == "invalid_filtered_view_mode"
    assert payload["details"] == {
        "view_mode": "latest",
        "valid_view_modes": ["errors", "head", "sample"],
    }


@pytest.mark.parametrize(("tool_name", "arguments"), PROJECT_PROTECTED_TOOL_CALL_ARGUMENTS)
async def test_project_protected_tools_api_require_bearer_token(
    jsonrpc: JsonRpcClient,
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    """Verify every project-protected tool rejects missing bearer tokens."""

    response = await jsonrpc.post(
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
async def test_project_protected_tools_api_reject_invalid_bearer_tokens(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    tool_name: str,
    arguments: dict[str, object],
    scopes: list[str],
    token_factory: ProjectProtectedInvalidTokenFactory,
    label: str,
) -> None:
    """Verify project-protected tools reject malformed, expired, or bad tokens."""

    response = await jsonrpc.post(
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
async def test_project_protected_tools_api_reject_project_access_mismatch(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    tool_name: str,
    arguments: dict[str, object],
    scopes: list[str],
) -> None:
    """Verify single-project tools reject valid tokens for unauthorized projects."""

    token: str = custom_jwt_token("agent", scopes, "agent")
    mismatched_arguments: dict[str, object] = {**arguments, "project_name": "other-project"}

    response = await jsonrpc.post(
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
async def test_snapshot_tools_api_accept_valid_bearer_token(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    """Verify snapshot tools accept valid tokens after a workflow snapshot exists."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        collect_response = await jsonrpc.post(
            token=valid_jwt_token,
            data=build_collect_logs_request(source_keys=["app_file"]),
        )
        tool_response = await jsonrpc.post(
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


async def test_list_projects_api_returns_manifest_backed_projects(
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

    response = await jsonrpc.post(
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


async def test_read_project_manifest_api_returns_authorized_manifest_contract(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify read_project_manifest returns detailed persisted manifest metadata."""

    token: str = custom_jwt_token(
        "workflow-agent",
        [PROJECTS_READ_SCOPE],
        "workflow-agent",
        {"projects_access": "all"},
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "read-project-manifest",
            "method": "tools/call",
            "params": {
                "name": "read_project_manifest",
                "arguments": {"project_name": "landingpage"},
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "read_project_manifest"
    assert payload["project_name"] == "landingpage"
    assert payload["project_summary"] == "Landingpage project for analysis tests."
    assert payload["source_keys"] == [
        "backend",
        "nginx",
        "app_file",
        "app_first",
        "app_second",
        "snapshot_text",
        "traefik",
    ]
    assert payload["static_asset_paths"] == ["/favicon.ico", "/robots.txt", "/sitemap.xml"]
    assert payload["static_asset_extensions"]
    backend_source = payload["sources"][0]
    assert backend_source["source_key"] == "backend"
    assert backend_source["source_type"] == "file"
    assert backend_source["target"].endswith("/src/tests/fixtures/logs/landingpage/backend.log")
    assert backend_source["description"] == "Backend logs."
    assert backend_source["required"] is True
    assert backend_source["parser_type"] == "json_lines"
    assert backend_source["normalization_profile"] == "app_logs"
    assert backend_source["retention_class"] == "short"
    assert backend_source["default_noise_profile"] == "backend_noise"
    assert backend_source["stream"] is None
    assert backend_source["inspect_path_prefixes"] == []
    assert "id" not in payload
    assert "created_at" not in payload
    assert "updated_at" not in payload


async def test_read_project_manifest_api_allows_session_caller_without_session_id(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify manifest inspection is project-scoped and does not require session_id."""

    await McpCaller.objects.filter(
        client_id="codex-agent",
        client_type="codex",
        workspace=LogWorkspace.WORKFLOW,
    ).delete()
    token: str = custom_jwt_token(
        "codex-agent",
        [PROJECTS_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["landingpage"], "client_type": "codex"},
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "read-project-manifest-session-caller",
            "method": "tools/call",
            "params": {
                "name": "read_project_manifest",
                "arguments": {"project_name": "landingpage"},
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "read_project_manifest"
    assert payload["project_name"] == "landingpage"


async def test_read_project_manifest_api_filters_to_one_source(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify read_project_manifest can return one source definition."""

    token: str = custom_jwt_token(
        "workflow-agent",
        [PROJECTS_READ_SCOPE],
        "workflow-agent",
        {"projects_access": "all"},
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "read-project-manifest-source",
            "method": "tools/call",
            "params": {
                "name": "read_project_manifest",
                "arguments": {"project_name": "landingpage", "source_key": "traefik"},
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["requested_source_key"] == "traefik"
    assert payload["source_keys"] == ["traefik"]
    assert [source["source_key"] for source in payload["sources"]] == ["traefik"]
    assert payload["sources"][0]["normalization_profile"] == "proxy_access"


async def test_read_project_manifest_api_returns_unknown_source_error(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify read_project_manifest gives structured guidance for unknown sources."""

    token: str = custom_jwt_token(
        "workflow-agent",
        [PROJECTS_READ_SCOPE],
        "workflow-agent",
        {"projects_access": "all"},
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "read-project-manifest-missing-source",
            "method": "tools/call",
            "params": {
                "name": "read_project_manifest",
                "arguments": {"project_name": "landingpage", "source_key": "missing"},
            },
        },
    )

    result = response.json()["result"]
    payload = result["structuredContent"]

    assert response.status_code == 200
    assert result["isError"] is True
    assert payload["error_code"] == "unknown_source_key"
    assert payload["details"] == {
        "project_name": "landingpage",
        "source_key": "missing",
        "available_source_keys": [
            "backend",
            "nginx",
            "app_file",
            "app_first",
            "app_second",
            "snapshot_text",
            "traefik",
        ],
    }


async def test_read_container_file_api_returns_file_contents(
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

    response = await jsonrpc.post(
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


async def test_inspect_containers_health_api_returns_container_status(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify inspect_containers_health returns all project docker source states."""

    token: str = custom_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["dockerpage"]},
    )

    mocker.patch(
        "tools.container_inspection.docker_service.inspect_container_health",
        return_value=ContainerHealth(
            container_id="abc123def456",
            container_name="app-container",
            image="portfolio/backend:2026-05-16",
            docker_status="running",
            health_status="healthy",
            running=True,
            restarting=False,
            paused=False,
            dead=False,
            exit_code=0,
            error="",
            restart_count=2,
            started_at="2026-05-16T10:00:00.000000000Z",
            finished_at="0001-01-01T00:00:00Z",
        ),
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "inspect-container-health",
            "method": "tools/call",
            "params": {
                "name": "inspect_containers_health",
                "arguments": {
                    "project_name": "dockerpage",
                },
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "inspect_containers_health"
    assert payload["project_name"] == "dockerpage"
    assert payload["resolved_source_keys"] == ["backend", "frontend", "nginx"]
    assert len(payload["containers"]) == 3
    backend_payload = payload["containers"][0]
    assert backend_payload["source_key"] == "backend"
    assert backend_payload["container_name"] == "app-container"
    assert backend_payload["docker_status"] == "running"
    assert backend_payload["health_status"] == "healthy"
    assert backend_payload["running"] is True
    assert backend_payload["restart_count"] == 2
    assert backend_payload["image"] == "portfolio/backend:2026-05-16"


async def test_inspect_vps_containers_api_returns_docker_ps_inventory(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify inspect_vps_containers returns bounded VPS-wide container facts."""

    token: str = custom_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["all"]},
    )

    mocker.patch(
        "tools.container_inspection.docker_service.inspect_vps_containers",
        return_value=[
            VpsContainerInventory(
                container_id="abc123def4567890",
                short_container_id="abc123def456",
                container_name="backend-container",
                image="portfolio/backend:2026-05-16",
                command=["gunicorn", "app.wsgi:application"],
                command_preview="gunicorn app.wsgi:application",
                created_at="2026-05-16T09:55:00.000000000Z",
                docker_status="running",
                state="running",
                health_status="unhealthy",
                running=True,
                restarting=False,
                paused=False,
                dead=False,
                exit_code=0,
                error="",
                restart_count=8,
                started_at="2026-05-16T10:00:00.000000000Z",
                finished_at=None,
                compose_labels={
                    "com.docker.compose.project": "portfolio",
                    "com.docker.compose.service": "backend",
                },
                restart_policy=ContainerRestartPolicy(
                    name="unless-stopped",
                    maximum_retry_count=0,
                ),
                ports=[
                    ContainerDetailPort(
                        private_port="8000/tcp",
                        host_ip="127.0.0.1",
                        host_port="18080",
                    )
                ],
                network_names=["web"],
                triage_notes=["health_status=unhealthy", "restart_count=8"],
            )
        ],
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "inspect-vps-containers",
            "method": "tools/call",
            "params": {
                "name": "inspect_vps_containers",
                "arguments": {},
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "inspect_vps_containers"
    assert payload["container_count"] == 1
    assert payload["truncated"] is False
    assert payload["containers"][0]["container_name"] == "backend-container"
    assert payload["containers"][0]["command_preview"] == "gunicorn app.wsgi:application"
    assert payload["containers"][0]["compose_labels"] == {
        "com.docker.compose.project": "portfolio",
        "com.docker.compose.service": "backend",
    }
    assert payload["containers"][0]["restart_policy"] == {
        "name": "unless-stopped",
        "maximum_retry_count": 0,
    }
    assert payload["containers"][0]["ports"] == [
        {"private_port": "8000/tcp", "host_ip": "127.0.0.1", "host_port": "18080"}
    ]
    assert payload["containers"][0]["network_names"] == ["web"]
    assert payload["containers"][0]["triage_notes"] == [
        "health_status=unhealthy",
        "restart_count=8",
    ]
    assert "secret" not in json.dumps(payload).lower()


async def test_inspect_vps_volumes_api_returns_volume_inventory(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify inspect_vps_volumes returns redacted VPS-wide volume facts."""

    token: str = custom_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["dockerpage"]},
    )

    mocker.patch(
        "tools.container_inspection.docker_service.inspect_vps_volumes",
        return_value=[
            VpsVolumeInventory(
                volume_name="dockerpage_db_data",
                driver="local",
                scope="local",
                created_at="2026-05-16T09:55:00Z",
                compose_labels={
                    "com.docker.compose.project": "dockerpage",
                    "com.docker.compose.volume": "db_data",
                },
                option_keys=["type"],
                mountpoint_available=True,
                mountpoint_redacted=True,
                usage_ref_count=2,
                usage_size_bytes=4096,
            )
        ],
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "inspect-vps-volumes",
            "method": "tools/call",
            "params": {
                "name": "inspect_vps_volumes",
                "arguments": {},
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "inspect_vps_volumes"
    assert payload["volume_count"] == 1
    assert payload["truncated"] is False
    assert payload["volumes"] == [
        {
            "volume_name": "dockerpage_db_data",
            "driver": "local",
            "scope": "local",
            "created_at": "2026-05-16T09:55:00Z",
            "compose_labels": {
                "com.docker.compose.project": "dockerpage",
                "com.docker.compose.volume": "db_data",
            },
            "option_keys": ["type"],
            "mountpoint_available": True,
            "mountpoint_redacted": True,
            "usage_ref_count": 2,
            "usage_size_bytes": 4096,
        }
    ]
    assert payload["volumes"][0]["mountpoint_available"] is True
    assert payload["volumes"][0]["mountpoint_redacted"] is True
    assert "/var/lib/docker" not in json.dumps(payload)


async def test_inspect_vps_volumes_api_forwards_volume_filters(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify inspect_vps_volumes accepts cleanup-oriented inventory filters."""

    token: str = custom_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["dockerpage"]},
    )

    inspect_mock = mocker.patch(
        "tools.container_inspection.docker_service.inspect_vps_volumes",
        return_value=[],
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "inspect-vps-volumes-filtered",
            "method": "tools/call",
            "params": {
                "name": "inspect_vps_volumes",
                "arguments": {
                    "dangling_only": True,
                    "anonymous_only": True,
                    "name_prefix": "a",
                },
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    inspect_mock.assert_called_once_with(
        dangling_only=True,
        anonymous_only=True,
        name_prefix="a",
    )
    assert payload["filters"] == {
        "dangling_only": True,
        "anonymous_only": True,
        "name_prefix": "a",
    }


async def test_inspect_tls_certificate_api_returns_site_domain_summary(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify inspect_tls_certificate returns bounded SITE_DOMAIN certificate facts."""

    token: str = custom_jwt_token(
        "codex-agent",
        [MCP_STATUS_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["landingpage"], "client_type": "codex"},
    )
    mocker.patch(
        "tools.tls.tls_certificate_service.inspect_site_certificate",
        return_value=TlsCertificateInspection(
            domain_key="site",
            hostname="example.com",
            port=443,
            inspection_status="ok",
            warning_level="ok",
            subject_summary="CN=example.com",
            issuer_summary="CN=Example CA",
            not_before="2025-12-01T00:00:00+00:00",
            not_after="2026-04-01T00:00:00+00:00",
            days_until_expiry=90,
            hostname_matches=True,
            matched_names=["example.com"],
            error_code=None,
            message="TLS certificate is valid for SITE_DOMAIN.",
        ),
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "inspect-tls-certificate",
            "method": "tools/call",
            "params": {"name": "inspect_tls_certificate", "arguments": {}},
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "inspect_tls_certificate"
    assert payload["domain_key"] == "site"
    assert payload["hostname"] == "example.com"
    assert payload["port"] == 443
    assert payload["inspection_status"] == "ok"
    assert payload["warning_level"] == "ok"
    assert payload["hostname_matches"] is True


async def test_inspect_container_detail_api_returns_curated_container_metadata(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify inspect_container_detail returns one bounded docker-inspect view."""

    token: str = custom_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["dockerpage"]},
    )

    mocker.patch(
        "tools.container_inspection.docker_service.inspect_container_detail",
        return_value=ContainerDetail(
            health=ContainerHealth(
                container_id="abc123def456",
                container_name="app-container",
                image="portfolio/backend:2026-05-16",
                docker_status="running",
                health_status="healthy",
                running=True,
                restarting=False,
                paused=False,
                dead=False,
                exit_code=0,
                error="",
                restart_count=2,
                started_at="2026-05-16T10:00:00.000000000Z",
                finished_at=None,
            ),
            created_at="2026-05-16T09:55:00.000000000Z",
            env_var_names=[
                "SECRET_KEY",
                "DJANGO_SETTINGS_MODULE",
                "DATABASE_URL",
                "NODE_ENV",
                "CUSTOM_VALUE",
            ],
            env_vars=[
                ContainerDetailEnvVar(
                    name="SECRET_KEY",
                    value=None,
                    value_redacted=True,
                    secret=True,
                ),
                ContainerDetailEnvVar(
                    name="DJANGO_SETTINGS_MODULE",
                    value="app.settings",
                    value_redacted=False,
                    secret=False,
                ),
                ContainerDetailEnvVar(
                    name="DATABASE_URL",
                    value=None,
                    value_redacted=True,
                    secret=True,
                ),
                ContainerDetailEnvVar(
                    name="NODE_ENV",
                    value="production",
                    value_redacted=False,
                    secret=False,
                ),
                ContainerDetailEnvVar(
                    name="CUSTOM_VALUE",
                    value=None,
                    value_redacted=True,
                    secret=False,
                ),
            ],
            label_keys=["com.docker.compose.service"],
            compose_labels={"com.docker.compose.service": "backend"},
            restart_policy=ContainerRestartPolicy(
                name="unless-stopped",
                maximum_retry_count=3,
            ),
            command=["gunicorn", "app.wsgi:application"],
            entrypoint=["/entrypoint.sh"],
            working_dir="/app",
            user="app",
            ports=[
                ContainerDetailPort(
                    private_port="8000/tcp",
                    host_ip="127.0.0.1",
                    host_port="18080",
                )
            ],
            mounts=[
                ContainerDetailMount(
                    type="bind",
                    destination="/app",
                    mode="rw",
                    rw=True,
                )
            ],
            networks=[
                ContainerDetailNetwork(
                    name="web",
                    ip_address="172.20.0.10",
                    aliases=["backend", "api"],
                )
            ],
            health_log=[
                {
                    "start": "2026-05-16T10:01:00.000000000Z",
                    "end": "2026-05-16T10:01:01.000000000Z",
                    "exit_code": 0,
                    "output": "ok\n",
                }
            ],
        ),
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "inspect-container-detail",
            "method": "tools/call",
            "params": {
                "name": "inspect_container_detail",
                "arguments": {
                    "project_name": "dockerpage",
                    "source_key": "backend",
                },
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "inspect_container_detail"
    assert payload["project_name"] == "dockerpage"
    assert payload["source_key"] == "backend"
    assert payload["container"]["container_name"] == "app-container"
    assert payload["created_at"] == "2026-05-16T09:55:00.000000000Z"
    assert payload["env_var_names"] == [
        "SECRET_KEY",
        "DJANGO_SETTINGS_MODULE",
        "DATABASE_URL",
        "NODE_ENV",
        "CUSTOM_VALUE",
    ]
    assert payload["env_vars"] == [
        {"name": "SECRET_KEY", "value": None, "value_redacted": True, "secret": True},
        {
            "name": "DJANGO_SETTINGS_MODULE",
            "value": "app.settings",
            "value_redacted": False,
            "secret": False,
        },
        {"name": "DATABASE_URL", "value": None, "value_redacted": True, "secret": True},
        {
            "name": "NODE_ENV",
            "value": "production",
            "value_redacted": False,
            "secret": False,
        },
        {"name": "CUSTOM_VALUE", "value": None, "value_redacted": True, "secret": False},
    ]
    assert "hidden" not in json.dumps(payload)
    assert "postgres://user:pass@db/app" not in json.dumps(payload)
    assert "should-not-leak" not in json.dumps(payload)
    assert payload["label_keys"] == ["com.docker.compose.service"]
    assert payload["compose_labels"] == {"com.docker.compose.service": "backend"}
    assert payload["restart_policy"] == {
        "name": "unless-stopped",
        "maximum_retry_count": 3,
    }
    assert payload["command"] == ["gunicorn", "app.wsgi:application"]
    assert payload["entrypoint"] == ["/entrypoint.sh"]
    assert payload["working_dir"] == "/app"
    assert payload["user"] == "app"
    assert payload["ports"] == [
        {"private_port": "8000/tcp", "host_ip": "127.0.0.1", "host_port": "18080"}
    ]
    assert payload["mounts"] == [{"type": "bind", "destination": "/app", "mode": "rw", "rw": True}]
    assert payload["networks"] == [
        {"name": "web", "ip_address": "172.20.0.10", "aliases": ["backend", "api"]}
    ]
    assert payload["health_log"][0]["output"] == "ok\n"


async def test_stat_container_path_api_returns_file_metadata(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify stat_container_path returns whitelisted container path metadata."""

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

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "stat-container-path",
            "method": "tools/call",
            "params": {
                "name": "stat_container_path",
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
    assert payload["action"] == "stat_container_path"
    assert payload["project_name"] == "dockerpage"
    assert payload["source_key"] == "backend"
    assert payload["container_name"] == "app-container"
    assert payload["path"] == "/app/VERSION"
    assert payload["file"]["name"] == "VERSION"
    assert payload["file"]["size"] == 12


async def test_inspect_project_compose_state_api_returns_comparison(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify compose-state inspection returns runtime-derived Compose state."""

    token: str = custom_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["dockerpage"]},
    )
    mocker.patch(
        "tools.container_inspection.docker_service.inspect_vps_containers",
        return_value=[
            VpsContainerInventory(
                container_id="abc123def4567890",
                short_container_id="abc123def456",
                container_name="app-container",
                image="portfolio/backend:2026-05-17",
                command=[],
                command_preview="",
                created_at=None,
                docker_status="running",
                state="running",
                health_status="healthy",
                running=True,
                restarting=False,
                paused=False,
                dead=False,
                exit_code=0,
                error="",
                restart_count=0,
                started_at=None,
                finished_at=None,
                compose_labels={
                    "com.docker.compose.project": "dockerpage",
                    "com.docker.compose.service": "backend",
                },
                restart_policy=ContainerRestartPolicy(
                    name="unless-stopped",
                    maximum_retry_count=0,
                ),
                ports=[
                    ContainerDetailPort(
                        private_port="8000/tcp",
                        host_ip="127.0.0.1",
                        host_port="18080",
                    )
                ],
                network_names=[],
                triage_notes=[],
                env_var_names=["SECRET_KEY"],
                mounts=[
                    ContainerDetailMount(
                        type="volume",
                        destination="/app",
                        mode="rw",
                        rw=True,
                        name="dockerpage_static",
                    )
                ],
            )
        ],
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "inspect-project-compose-state",
            "method": "tools/call",
            "params": {
                "name": "inspect_project_compose_state",
                "arguments": {"project_name": "dockerpage"},
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "inspect_project_compose_state"
    assert payload["project_name"] == "dockerpage"
    assert payload["compose_project"] == "dockerpage"
    assert payload["expected_services"][0] == {
        "source_key": "backend",
        "compose_project": "dockerpage",
        "service_name": "backend",
    }
    assert payload["running_containers"][0]["mounts"][0]["source_redacted"] is True
    assert payload["running_containers"][0]["volume_names"] == ["dockerpage_static"]
    assert payload["warnings"] == []
    assert "hidden" not in json.dumps(payload)


async def test_inspect_project_compose_state_api_requires_expected_state(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify compose-state inspection requires labelled target containers."""

    token: str = custom_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["dockerpage"]},
    )
    mocker.patch(
        "tools.container_inspection.docker_service.inspect_vps_containers",
        return_value=[],
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "inspect-project-compose-state-unavailable",
            "method": "tools/call",
            "params": {
                "name": "inspect_project_compose_state",
                "arguments": {"project_name": "dockerpage"},
            },
        },
    )

    result = response.json()["result"]
    payload = result["structuredContent"]

    assert response.status_code == 200
    assert result["isError"] is True
    assert payload["action"] == "inspect_project_compose_state"
    assert payload["error_code"] == "compose_expected_state_unavailable"


@pytest.mark.parametrize(
    ("arguments", "expected_path"),
    [
        ({"project_name": "dockerpage", "source_key": "backend", "path": "/app"}, "/app"),
        ({"project_name": "dockerpage", "source_key": "backend"}, "/app"),
    ],
)
async def test_list_container_directory_api_returns_entries(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
    arguments: dict[str, object],
    expected_path: str,
) -> None:
    """Verify list_container_directory returns whitelisted container entries."""

    token: str = custom_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["dockerpage"]},
    )

    list_directory = mocker.patch(
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

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "list-container-directory",
            "method": "tools/call",
            "params": {
                "name": "list_container_directory",
                "arguments": arguments,
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "list_container_directory"
    assert payload["project_name"] == "dockerpage"
    assert payload["path"] == expected_path
    assert payload["entries"][0]["name"] == "VERSION"
    list_directory.assert_called_once_with("app-container", expected_path)


async def test_stat_project_path_api_returns_file_metadata(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify stat_project_path returns metadata for a manifest file source."""

    token: str = custom_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["landingpage"], "client_type": "codex"},
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "stat-project-path",
            "method": "tools/call",
            "params": {
                "name": "stat_project_path",
                "arguments": {"project_name": "landingpage", "source_key": "app_file"},
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "stat_project_path"
    assert payload["project_name"] == "landingpage"
    assert payload["source_key"] == "app_file"
    assert payload["path"].endswith("/src/tests/fixtures/logs/landingpage/app_file.log")
    assert payload["file"]["exists"] is True
    assert payload["file"]["is_file"] is True
    assert payload["file"]["readable"] is True


async def test_stat_project_path_api_allows_session_caller_without_session_id(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify host path tools are project-scoped and do not require session_id."""

    await McpCaller.objects.filter(
        client_id="codex-agent",
        client_type="codex",
        workspace=LogWorkspace.WORKFLOW,
    ).delete()
    token: str = custom_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["landingpage"], "client_type": "codex"},
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "stat-project-path-session-caller",
            "method": "tools/call",
            "params": {
                "name": "stat_project_path",
                "arguments": {"project_name": "landingpage", "source_key": "app_file"},
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "stat_project_path"
    assert payload["project_name"] == "landingpage"
    assert payload["source_key"] == "app_file"


async def test_read_project_file_api_returns_bounded_file_contents(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify read_project_file returns a bounded text preview."""

    token: str = custom_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["landingpage"], "client_type": "codex"},
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "read-project-file",
            "method": "tools/call",
            "params": {
                "name": "read_project_file",
                "arguments": {
                    "project_name": "landingpage",
                    "source_key": "app_file",
                    "max_bytes": 12,
                },
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "read_project_file"
    assert payload["max_bytes"] == 12
    assert payload["truncated"] is True
    assert len(payload["content"].encode("utf-8")) <= 12
    assert payload["file"]["is_file"] is True


async def test_list_project_directory_api_returns_parent_entries(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify list_project_directory lists the approved source parent directory."""

    token: str = custom_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["landingpage"], "client_type": "codex"},
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "list-project-directory",
            "method": "tools/call",
            "params": {
                "name": "list_project_directory",
                "arguments": {"project_name": "landingpage", "source_key": "app_file"},
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]
    entry_names = [entry["name"] for entry in payload["entries"]]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "list_project_directory"
    assert payload["path"].endswith("/src/tests/fixtures/logs/landingpage")
    assert "app_file.log" in entry_names
    assert payload["truncated"] is False


async def test_stat_project_path_api_rejects_parent_traversal(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify host path inspection rejects traversal syntax before filesystem access."""

    token: str = custom_jwt_token(
        "codex-agent",
        [CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"allowed_projects": ["landingpage"], "client_type": "codex"},
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "stat-project-path-traversal",
            "method": "tools/call",
            "params": {
                "name": "stat_project_path",
                "arguments": {
                    "project_name": "landingpage",
                    "source_key": "app_file",
                    "path": "../secret.txt",
                },
            },
        },
    )

    result = response.json()["result"]
    payload = result["structuredContent"]

    assert response.status_code == 200
    assert result["isError"] is True
    assert payload["action"] == "stat_project_path"
    assert payload["error_code"] == "project_path_parent_traversal"
    assert payload["details"] == {"path": "../secret.txt"}


async def test_list_projects_api_returns_multiple_manifest_backed_projects(
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
    response = await jsonrpc.post(
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
        "vps-security",
    ]
    assert payload[0]["project_summary"] == "Alpha project summary."
    assert payload[1]["project_summary"] == "Beta project summary."
    assert payload[0]["source_keys"] == ["app_file"]
    assert payload[1]["source_keys"] == ["app_file"]


async def test_inspect_live_fail2ban_activity_api_returns_live_status(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify fail2ban diagnostics expose structured allowlisted live status."""

    token: str = custom_jwt_token(
        "agent",
        [MCP_STATUS_READ_SCOPE],
        "agent",
        {"allowed_projects": ["landingpage"]},
    )
    mocker.patch(
        "tools.fail2ban.fail2ban_service.inspect_activity",
        return_value=Fail2banActivity(
            inspection_status="ok",
            service=Fail2banServiceStatus(
                inspection_status="ok",
                jail_count=2,
                jails=["portfolio-nginx-probes", "portfolio-traefik-probes"],
            ),
            jails=[
                Fail2banJailStatus(
                    jail="portfolio-nginx-probes",
                    inspection_status="ok",
                    currently_failed=4,
                    total_failed=11,
                    currently_banned=2,
                    total_banned=3,
                    banned_ips=["203.0.113.10", "198.51.100.2"],
                ),
                Fail2banJailStatus(
                    jail="portfolio-traefik-probes",
                    inspection_status="ok",
                    currently_failed=0,
                    total_failed=5,
                    currently_banned=0,
                    total_banned=1,
                    banned_ips=[],
                ),
            ],
        ),
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "inspect-live-fail2ban-activity",
            "method": "tools/call",
            "params": {
                "name": "inspect_live_fail2ban_activity",
                "arguments": {"project_name": "landingpage"},
            },
        },
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["action"] == "inspect_live_fail2ban_activity"
    assert payload["project_name"] == "landingpage"
    assert payload["inspection_status"] == "ok"
    assert payload["service"]["jail_count"] == 2
    assert payload["jails"][0]["jail"] == "portfolio-nginx-probes"
    assert payload["jails"][0]["banned_ips"] == ["203.0.113.10", "198.51.100.2"]


async def test_collect_logs_api_returns_agent_error_for_project_mismatch(
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify collect_logs returns a structured project mismatch error."""

    response = await jsonrpc.post(
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
async def test_collect_logs_api_uses_all_accessible_projects_when_project_names_not_provided(
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
        response = await jsonrpc.post(
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


async def test_collect_logs_api_generates_session_id_before_tool_call(
    file_backed_project_context: FileBackedProjectContext,
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify MCP middleware injects session_id before collect_logs runs."""

    token = custom_jwt_token(
        "codex-agent",
        [LOGS_COLLECT_SCOPE, PROJECTS_READ_SCOPE],
        "codex-client",
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
        response = await jsonrpc.post(
            token=token,
            data=build_collect_logs_request(
                source_keys=["app_file"],
                session_id=None,
            ),
        )

    payload = response.json()["result"]["structuredContent"]
    session_id = payload["session_id"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert SESSION_ID_PATTERN.fullmatch(session_id)
    assert len(session_id) <= 24
    assert payload["workspace"] == "session"
    assert (
        file_backed_project_context.logs_dir
        / "sessions"
        / session_id
        / "landingpage"
        / "app_file.log"
    ).exists()


async def test_analysis_tool_rejects_snapshot_owned_by_other_caller(
    file_backed_project_context: FileBackedProjectContext,
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify tools compare loaded snapshot caller_id against the request caller."""

    owner_token = custom_jwt_token(
        "codex-agent",
        [LOGS_COLLECT_SCOPE, PROJECTS_READ_SCOPE],
        "codex-client",
        {"client_type": "codex"},
    )
    other_token = custom_jwt_token(
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
        collect_response = await jsonrpc.post(
            token=owner_token,
            data=build_collect_logs_request(
                source_keys=["app_file"],
                session_id=None,
            ),
        )
        session_id = collect_response.json()["result"]["structuredContent"]["session_id"]
        response = await jsonrpc.post(
            token=other_token,
            data={
                "jsonrpc": "2.0",
                "id": "group-errors-other-caller-snapshot",
                "method": "tools/call",
                "params": {
                    "name": "group_errors",
                    "arguments": {
                        "project_name": "landingpage",
                        "session_id": session_id,
                    },
                },
            },
        )

    payload = response.json()["result"]["structuredContent"]

    assert collect_response.status_code == 200
    assert collect_response.json()["result"]["isError"] is False
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert payload["error_code"] == "snapshot_not_found"
    assert payload["message"] == "Requested session log snapshot was not found."


async def test_collect_logs_api_uses_caller_workspace_when_workspace_is_omitted(
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
    mocker: MockerFixture,
) -> None:
    """Verify collect_logs injects the authenticated caller workspace."""

    create_spy = mocker.patch(
        "middleware.audit.agent_call_audit_service.create_tool_call",
        new=mocker.AsyncMock(return_value=uuid4()),
    )
    mocker.patch(
        "middleware.audit.agent_call_audit_service.complete_tool_call",
        new=mocker.AsyncMock(),
    )

    response = await jsonrpc.post(
        token=valid_jwt_token,
        data=build_collect_logs_request(
            source_keys=["app_file"],
            workspace=None,
            session_id=None,
        ),
    )

    payload = response.json()["result"]["structuredContent"]

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is False
    assert payload["workspace"] == "workflow"
    create_spy.assert_awaited_once()


async def test_workflow_skill_resource_read_api_returns_skill_contents(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify workflow skill resources return the requested skill text."""

    workflow_token: str = custom_jwt_token(
        "workflow-agent",
        [WORKFLOW_SKILLS_READ_SCOPE],
        "workflow-agent",
    )

    response = await jsonrpc.post(
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
    assert contents[0]["text"]


async def test_bot_detection_skill_describes_misleading_infra_warning_probes(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify bot detection guidance teaches infra-warning probe reasoning."""

    workflow_token: str = custom_jwt_token(
        "workflow-agent",
        [WORKFLOW_SKILLS_READ_SCOPE],
        "workflow-agent",
    )

    response = await jsonrpc.post(
        token=workflow_token,
        data={
            "jsonrpc": "2.0",
            "id": "4",
            "method": "resources/read",
            "params": {"uri": "skill://workflow/bot_detection"},
        },
    )

    contents = response.json()["result"]["contents"]

    assert response.status_code == 200
    assert "Misleading infrastructure-warning probes" in contents[0]["text"]
    assert "Noise-vs-incident reasoning checklist" in contents[0]["text"]
    assert "multiple deterministic facts" in contents[0]["text"]
    assert "service impact" in contents[0]["text"]
    assert "ACME" in contents[0]["text"]
    assert "not a standalone rule" in contents[0]["text"]
    assert "very likely scanner noise" in contents[0]["text"]


async def test_workflow_skills_do_not_infer_mitigation_from_zero_current_bans(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify security-daemon guidance does not infer mitigation from zero bans."""

    workflow_token: str = custom_jwt_token(
        "workflow-agent",
        [WORKFLOW_SKILLS_READ_SCOPE],
        "workflow-agent",
    )

    bot_response = await jsonrpc.post(
        token=workflow_token,
        data={
            "jsonrpc": "2.0",
            "id": "bot",
            "method": "resources/read",
            "params": {"uri": "skill://workflow/bot_detection"},
        },
    )
    recommendations_response = await jsonrpc.post(
        token=workflow_token,
        data={
            "jsonrpc": "2.0",
            "id": "recommendations",
            "method": "resources/read",
            "params": {"uri": "skill://workflow/recommendations_guide"},
        },
    )
    severity_response = await jsonrpc.post(
        token=workflow_token,
        data={
            "jsonrpc": "2.0",
            "id": "severity",
            "method": "resources/read",
            "params": {"uri": "skill://workflow/severity_guide"},
        },
    )

    assert bot_response.status_code == 200
    assert recommendations_response.status_code == 200
    assert severity_response.status_code == 200
    bot_text = bot_response.json()["result"]["contents"][0]["text"]
    recommendations_text = recommendations_response.json()["result"]["contents"][0]["text"]
    severity_text = severity_response.json()["result"]["contents"][0]["text"]

    assert "Zero currently banned IPs only means no IPs are banned" in bot_text
    assert "do not treat it as evidence of past mitigation" in bot_text
    assert "Do not describe traffic as" in recommendations_text
    assert "blocked, mitigated, or effectively handled" in recommendations_text
    assert "active mitigation such as fail2ban" not in bot_text
    assert "fail2ban is active and blocking" not in recommendations_text
    assert "detected and blocked by fail2ban" not in severity_text


async def test_resources_list_shows_concrete_workflow_skill_resources(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify resources/list exposes concrete workflow skill resources."""

    workflow_token: str = custom_jwt_token(
        "workflow-agent",
        [WORKFLOW_SKILLS_READ_SCOPE],
        "workflow-agent",
    )

    response = await jsonrpc.post(
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
    assert "skill://workflow/project_context" not in resource_uris
    assert "skill://workflow/severity_guide" in resource_uris
    assert "skill://workflow/bot_detection" in resource_uris


async def test_resource_templates_list_exposes_workflow_skill_template(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify resource templates expose the workflow skill URI template."""

    workflow_token: str = custom_jwt_token(
        "workflow-agent",
        [WORKFLOW_SKILLS_READ_SCOPE],
        "workflow-agent",
    )

    response = await jsonrpc.post(
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


async def test_invalid_workflow_skill_resource_returns_agent_guidance(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify unknown workflow skill resources return actionable guidance."""

    workflow_token: str = custom_jwt_token(
        "workflow-agent",
        [WORKFLOW_SKILLS_READ_SCOPE],
        "workflow-agent",
    )

    response = await jsonrpc.post(
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


async def test_codex_cannot_access_workflow_components(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify Codex-scoped tokens cannot access workflow-only tools or resources."""

    codex_token: str = custom_jwt_token(
        "codex-agent",
        [PROJECTS_READ_SCOPE, LOGS_COLLECT_SCOPE, MCP_STATUS_READ_SCOPE, MCP_HEALTH_READ_SCOPE],
        "codex-agent",
    )

    tool_response = await jsonrpc.post(
        token=codex_token,
        data={
            "jsonrpc": "2.0",
            "id": "5",
            "method": "tools/call",
            "params": {"name": "analyze_daily_log_bundle", "arguments": {}},
        },
    )
    resource_response = await jsonrpc.post(
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


async def test_api_returns_structured_error_for_unknown_tool(
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify unknown tools include a stable structured error payload."""

    response = await jsonrpc.post(
        token=valid_jwt_token,
        data={
            "jsonrpc": "2.0",
            "id": "unknown-tool",
            "method": "tools/call",
            "params": {"name": "does_not_exist", "arguments": {}},
        },
    )

    result = response.json()["result"]

    assert response.status_code == 200
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "unknown_tool"
    assert result["structuredContent"]["details"] == {"tool_name": "does_not_exist"}


async def test_api_returns_structured_error_for_tool_argument_type_mismatch(
    file_backed_project_context: FileBackedProjectContext,
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify FastMCP validation errors are normalized for agents."""

    with override_settings(LOGS_DIR=file_backed_project_context.logs_dir):
        response = await jsonrpc.post(
            token=valid_jwt_token,
            data={
                "jsonrpc": "2.0",
                "id": "bad-source-keys-type",
                "method": "tools/call",
                "params": {
                    "name": "grep_log_snapshot",
                    "arguments": {
                        "project_name": "landingpage",
                        "source_keys": "frontend",
                        "grep": "GET",
                    },
                },
            },
        )

    result = response.json()["result"]
    payload = result["structuredContent"]

    assert response.status_code == 200
    assert result["isError"] is True
    assert payload["error_code"] == "invalid_tool_arguments"
    assert payload["message"] == "Tool arguments failed validation."
    assert payload["details"]["tool_name"] == "grep_log_snapshot"
    assert payload["details"]["invalid_arguments"] == ["source_keys"]


async def test_api_returns_fastmcp_error_for_unknown_jsonrpc_method(
    valid_jwt_token: str,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify unknown JSON-RPC methods use FastMCP's native error body."""

    response = await jsonrpc.post(
        token=valid_jwt_token,
        data={
            "jsonrpc": "2.0",
            "id": "unknown-method",
            "method": "tools/nope",
            "params": {},
        },
    )

    payload = response.json()["error"]

    assert response.status_code == 200
    assert payload["code"] == -32602
    assert payload["message"] == "Invalid request parameters"


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("tools/list", {}),
        ("tools/call", {"name": "list_projects", "arguments": {}}),
        ("resources/read", {"uri": "skill://workflow/severity_guide"}),
    ],
)
async def test_api_requires_bearer_token(
    jsonrpc: JsonRpcClient,
    method: str,
    params: dict[str, object],
) -> None:
    """Verify protected MCP methods require a bearer token."""

    response = await jsonrpc.post(
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
async def test_api_rejects_invalid_bearer_tokens(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    token_factory: InvalidTokenFactory,
    label: str,
) -> None:
    """Verify protected MCP methods reject invalid bearer tokens."""

    response = await jsonrpc.post(
        token=token_factory(custom_jwt_token),
        data={"jsonrpc": "2.0", "id": f"invalid-{label}", "method": "tools/list", "params": {}},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


async def test_tool_call_api_rejects_jwt_without_client_id(
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

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "missing-client-id",
            "method": "tools/call",
            "params": {
                "name": "close_agent_session",
                "arguments": {"session_id": "gentle-river-finds-a8f2"},
            },
        },
    )
    result = response.json()["result"]
    error_text = result["content"][0]["text"]

    assert response.status_code == 200
    assert result["isError"] is True
    assert "Authenticated JWT must include a non-empty client_id." in error_text
    assert "client_id" in error_text


async def test_tool_call_api_rejects_jwt_for_unregistered_client(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
) -> None:
    """Verify tool calls require a matching McpCaller row."""

    token = custom_jwt_token(
        "unregistered-agent",
        [SESSION_CLOSE_SCOPE],
        "unregistered-agent",
        {"client_type": "codex"},
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": "unregistered-client",
            "method": "tools/call",
            "params": {
                "name": "close_agent_session",
                "arguments": {"session_id": "gentle-river-finds-a8f2"},
            },
        },
    )
    result = response.json()["result"]
    error_text = result["content"][0]["text"]

    assert response.status_code == 200
    assert result["isError"] is True
    assert "Authenticated MCP client is not allowed to call tools." in error_text


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("tools/list", {}),
        ("resources/list", {}),
        ("resources/templates/list", {}),
        ("resources/read", {"uri": "skill://workflow/severity_guide"}),
    ],
)
async def test_discovery_api_rejects_jwt_for_unregistered_client(
    custom_jwt_token: CustomJwtToken,
    jsonrpc: JsonRpcClient,
    method: str,
    params: dict[str, object],
) -> None:
    """Verify discovery and resource reads require a matching McpCaller row."""

    token = custom_jwt_token(
        "unregistered-agent",
        [PROJECTS_READ_SCOPE, WORKFLOW_SKILLS_READ_SCOPE],
        "unregistered-agent",
        {"client_type": "codex"},
    )

    response = await jsonrpc.post(
        token=token,
        data={
            "jsonrpc": "2.0",
            "id": f"unregistered-{method}",
            "method": method,
            "params": params,
        },
    )
    payload = response.json()["error"]

    assert response.status_code == 200
    assert "Authenticated MCP client is not allowed to call tools." in payload["message"]
