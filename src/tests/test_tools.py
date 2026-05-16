import asyncio

from fastmcp.server.auth import require_scopes
from fastmcp.utilities.components import FastMCPComponent

from app import create_application
from auth.scopes import (
    CONTAINER_FILES_READ_SCOPE,
    LOGS_COLLECT_SCOPE,
    MCP_HEALTH_READ_SCOPE,
    MCP_STATUS_READ_SCOPE,
    PROJECTS_READ_SCOPE,
    WORKFLOW_BOOTSTRAP_SCOPE,
)
from middleware.audit import AccessAuditMiddleware
from tests.conftest import CustomAccessToken
from tools.agent_hints import (
    BUILD_INCIDENT_BUNDLE_TOOL_DESCRIPTION,
    CLOSE_AGENT_SESSION_TOOL_DESCRIPTION,
    COLLECT_LOGS_TOOL_DESCRIPTION,
    CREATE_FILTERED_VIEW_TOOL_DESCRIPTION,
    GREP_LOG_SNAPSHOT_TOOL_DESCRIPTION,
    GROUP_ERRORS_TOOL_DESCRIPTION,
    LIST_CONTAINER_DIRECTORY_TOOL_DESCRIPTION,
    READ_CONTAINER_FILE_TOOL_DESCRIPTION,
    STAT_CONTAINER_PATH_TOOL_DESCRIPTION,
    SUGGEST_FOLLOWUP_WINDOW_TOOL_DESCRIPTION,
)
from tools.workflow import build_workflow_bootstrap_payload, get_allowed_workflow_tool_metadata
from utils.assets import WorkflowAssetLoader


def test_application_registers_expected_mcp_components(
    custom_access_token: CustomAccessToken,
) -> None:
    app = create_application()

    async def run_test() -> None:
        tools = await app._local_provider.list_tools()
        tool_names = [tool.name for tool in tools]
        assert any(isinstance(middleware, AccessAuditMiddleware) for middleware in app.middleware)

        assert "analyze_daily_log_bundle" in tool_names
        assert "collect_logs" in tool_names
        assert "list_log_snapshot_files" in tool_names
        assert "read_log_snapshot_file" in tool_names
        assert "grep_log_snapshot" in tool_names
        assert "group_errors" in tool_names
        assert "build_incident_bundle" in tool_names
        assert "create_filtered_view" in tool_names
        assert "suggest_followup_window" in tool_names
        assert "list_projects" in tool_names
        assert "stat_container_path" in tool_names
        assert "read_container_file" in tool_names
        assert "list_container_directory" in tool_names
        assert "close_agent_session" in tool_names
        assert "get_mcp_service_status" in tool_names
        assert "get_mcp_health_check" in tool_names
        assert "list_workflow_skills" not in tool_names
        assert "get_workflow_skill" not in tool_names

        resources = await app._local_provider.list_resources()
        resource_uris = [str(resource.uri) for resource in resources]

        assert "skill://workflow/project_context" in resource_uris
        assert "skill://workflow/severity_guide" in resource_uris
        assert "skill://workflow/bot_detection" in resource_uris

        resource_templates = await app._local_provider.list_resource_templates()
        assert len(resource_templates) == 1
        assert resource_templates[0].name == "workflow_skill_resource"
        assert str(resource_templates[0].uri_template) == "skill://workflow/{skill_name}"

        workflow_token = custom_access_token(
            "workflow-agent",
            [
                WORKFLOW_BOOTSTRAP_SCOPE,
                LOGS_COLLECT_SCOPE,
                PROJECTS_READ_SCOPE,
                MCP_STATUS_READ_SCOPE,
                MCP_HEALTH_READ_SCOPE,
            ],
            "workflow-agent",
            {
                "client_type": "workflow_agent",
                "allowed_projects": ["landingpage"],
            },
        )
        bootstrap_text = build_workflow_bootstrap_payload(WorkflowAssetLoader(), workflow_token)
        assert bootstrap_text["workflow_name"] == "analyze_daily_log_bundle"
        assert "Log Summary Instructions" in bootstrap_text["prompt"]
        assert any(
            item["skill_name"] == "severity_guide" for item in bootstrap_text["mandatory_skills"]
        )
        assert any(
            item["skill_name"] == "recommendations_guide"
            for item in bootstrap_text["mandatory_skills"]
        )
        assert any(
            item["skill_name"] == "project_context" for item in bootstrap_text["mandatory_skills"]
        )
        assert any(
            item["skill_name"] == "bot_detection" for item in bootstrap_text["optional_skills"]
        )
        assert any(item["tool_name"] == "collect_logs" for item in bootstrap_text["tools"])
        assert any(
            item["tool_name"] == "list_log_snapshot_files" for item in bootstrap_text["tools"]
        )
        assert any(
            item["tool_name"] == "read_log_snapshot_file" for item in bootstrap_text["tools"]
        )
        assert any(item["tool_name"] == "grep_log_snapshot" for item in bootstrap_text["tools"])
        assert all(item["tool_name"] != "group_errors" for item in bootstrap_text["tools"])
        assert all(item["tool_name"] != "build_incident_bundle" for item in bootstrap_text["tools"])
        assert all(item["tool_name"] != "create_filtered_view" for item in bootstrap_text["tools"])
        assert any(
            item["tool_name"] == "suggest_followup_window" for item in bootstrap_text["tools"]
        )
        assert any(item["tool_name"] == "list_projects" for item in bootstrap_text["tools"])
        assert any(
            item["tool_name"] == "get_mcp_service_status" for item in bootstrap_text["tools"]
        )
        collect_logs_tool = next(
            item for item in bootstrap_text["tools"] if item["tool_name"] == "collect_logs"
        )
        assert collect_logs_tool["description"]
        assert any(
            argument["name"] == "project_names" for argument in collect_logs_tool["arguments"]
        )
        assert any(argument["name"] == "session_id" for argument in collect_logs_tool["arguments"])
        since_argument = next(
            argument for argument in collect_logs_tool["arguments"] if argument["name"] == "since"
        )
        assert since_argument["default"] == "24h"
        app_collect_tool = next(tool for tool in tools if tool.name == "collect_logs")
        assert app_collect_tool.description == COLLECT_LOGS_TOOL_DESCRIPTION
        app_incident_bundle_tool = next(
            tool for tool in tools if tool.name == "build_incident_bundle"
        )
        assert app_incident_bundle_tool.description == BUILD_INCIDENT_BUNDLE_TOOL_DESCRIPTION
        app_filtered_view_tool = next(tool for tool in tools if tool.name == "create_filtered_view")
        assert app_filtered_view_tool.description == CREATE_FILTERED_VIEW_TOOL_DESCRIPTION
        app_group_errors_tool = next(tool for tool in tools if tool.name == "group_errors")
        assert app_group_errors_tool.description == GROUP_ERRORS_TOOL_DESCRIPTION
        app_followup_tool = next(tool for tool in tools if tool.name == "suggest_followup_window")
        assert app_followup_tool.description == SUGGEST_FOLLOWUP_WINDOW_TOOL_DESCRIPTION
        app_close_session_tool = next(tool for tool in tools if tool.name == "close_agent_session")
        assert app_close_session_tool.description == CLOSE_AGENT_SESSION_TOOL_DESCRIPTION
        app_read_container_tool = next(tool for tool in tools if tool.name == "read_container_file")
        assert app_read_container_tool.description == READ_CONTAINER_FILE_TOOL_DESCRIPTION
        app_stat_container_tool = next(tool for tool in tools if tool.name == "stat_container_path")
        assert app_stat_container_tool.description == STAT_CONTAINER_PATH_TOOL_DESCRIPTION
        app_list_container_tool = next(
            tool for tool in tools if tool.name == "list_container_directory"
        )
        assert app_list_container_tool.description == LIST_CONTAINER_DIRECTORY_TOOL_DESCRIPTION
        assert "path" not in app_list_container_tool.parameters["required"]
        workflow_followup_tool = next(
            item
            for item in bootstrap_text["tools"]
            if item["tool_name"] == "suggest_followup_window"
        )
        assert workflow_followup_tool["description"] == SUGGEST_FOLLOWUP_WINDOW_TOOL_DESCRIPTION
        since_property = app_collect_tool.parameters["properties"]["since"]
        assert since_property["default"] == "24h"
        app_grep_tool = next(tool for tool in tools if tool.name == "grep_log_snapshot")
        assert app_grep_tool.description == GREP_LOG_SNAPSHOT_TOOL_DESCRIPTION
        assert "source_key" in app_grep_tool.parameters["properties"]
        assert "source_keys" in app_grep_tool.parameters["properties"]
        match_limit_property = app_grep_tool.parameters["properties"]["match_limit"]
        assert match_limit_property["default"] == 100
        request_caller_tool_names = {
            "get_mcp_service_status",
            "list_projects",
            "collect_logs",
            "list_log_snapshot_files",
            "read_log_snapshot_file",
            "grep_log_snapshot",
            "group_errors",
            "build_incident_bundle",
            "create_filtered_view",
            "stat_container_path",
            "read_container_file",
            "list_container_directory",
        }
        tools_by_name = {tool.name: tool for tool in tools}
        for tool_name in request_caller_tool_names:
            assert "caller" not in tools_by_name[tool_name].parameters["properties"]
        list_projects_tool = next(
            item for item in bootstrap_text["tools"] if item["tool_name"] == "list_projects"
        )
        assert list_projects_tool["arguments"] == []
        assert all(
            item["tool_name"] != "analyze_daily_log_bundle" for item in bootstrap_text["tools"]
        )

    asyncio.run(run_test())


def test_tool_metadata_filters_tools_by_token_scopes(
    custom_access_token: CustomAccessToken,
) -> None:
    codex_token = custom_access_token(
        "codex-agent",
        [MCP_HEALTH_READ_SCOPE, CONTAINER_FILES_READ_SCOPE],
        "codex-agent",
        {"client_type": "codex", "allowed_projects": ["landingpage"]},
    )

    allowed_tool_names = [
        entry["tool_name"] for entry in get_allowed_workflow_tool_metadata(codex_token)
    ]

    assert "get_mcp_health_check" in allowed_tool_names
    assert "read_container_file" not in allowed_tool_names
    assert "close_agent_session" not in allowed_tool_names
    assert "analyze_daily_log_bundle" not in allowed_tool_names
    assert "get_mcp_service_status" not in allowed_tool_names


def test_tool_auth_check_uses_token_scopes_per_request(
    custom_access_token: CustomAccessToken,
) -> None:
    workflow_check = require_scopes(WORKFLOW_BOOTSTRAP_SCOPE)
    workflow_token = custom_access_token(
        "workflow-agent",
        [WORKFLOW_BOOTSTRAP_SCOPE],
        "workflow-agent",
        {
            "client_type": "workflow_agent",
            "allowed_projects": ["landingpage"],
        },
    )
    codex_token = custom_access_token(
        "codex-agent",
        [MCP_HEALTH_READ_SCOPE],
        "codex-agent",
        {"client_type": "codex", "allowed_projects": ["landingpage"]},
    )

    from fastmcp.server.auth.authorization import AuthContext as FastMCPAuthContext

    component = FastMCPComponent(name="dummy-component")

    assert workflow_check(FastMCPAuthContext(token=workflow_token, component=component)) is True
    assert workflow_check(FastMCPAuthContext(token=codex_token, component=component)) is False


def test_workflow_bootstrap_uses_skill_resource_uris(
    custom_access_token: CustomAccessToken,
) -> None:
    workflow_token = custom_access_token(
        "workflow-agent",
        [
            WORKFLOW_BOOTSTRAP_SCOPE,
            LOGS_COLLECT_SCOPE,
            PROJECTS_READ_SCOPE,
            MCP_STATUS_READ_SCOPE,
            MCP_HEALTH_READ_SCOPE,
        ],
        "workflow-agent",
        {
            "client_type": "workflow_agent",
            "allowed_projects": ["landingpage"],
        },
    )

    payload = build_workflow_bootstrap_payload(WorkflowAssetLoader(), workflow_token)

    assert any(
        item["resource_uri"] == "skill://workflow/project_context"
        for item in payload["mandatory_skills"]
    )
    assert any(
        item["resource_uri"] == "skill://workflow/bot_detection"
        for item in payload["optional_skills"]
    )
