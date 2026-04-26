import asyncio

from fastmcp.server.auth import AccessToken, require_scopes
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
from tools.workflow import build_workflow_bootstrap_payload, get_allowed_workflow_tool_metadata
from utils.assets import WorkflowAssetLoader


def test_application_registers_expected_mcp_components() -> None:
    app = create_application()

    async def run_test() -> None:
        tools = await app._local_provider.list_tools()
        tool_names = [tool.name for tool in tools]

        assert "analyze_daily_log_bundle" in tool_names
        assert "collect_logs" in tool_names
        assert "list_projects" in tool_names
        assert "read_container_file" in tool_names
        assert "stat_container_path" in tool_names
        assert "list_container_directory" in tool_names
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

        workflow_token = AccessToken(
            token="workflow-dev-token",
            client_id="workflow-agent",
            scopes=[
                WORKFLOW_BOOTSTRAP_SCOPE,
                LOGS_COLLECT_SCOPE,
                PROJECTS_READ_SCOPE,
                MCP_STATUS_READ_SCOPE,
                MCP_HEALTH_READ_SCOPE,
            ],
            claims={
                "sub": "workflow-agent",
                "client_type": "workflow_agent",
                "project_key": "landingpage",
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
        assert any(item["tool_name"] == "list_projects" for item in bootstrap_text["tools"])
        assert any(
            item["tool_name"] == "get_mcp_service_status" for item in bootstrap_text["tools"]
        )
        collect_logs_tool = next(
            item for item in bootstrap_text["tools"] if item["tool_name"] == "collect_logs"
        )
        assert collect_logs_tool["description"]
        assert any(
            argument["name"] == "project_name" for argument in collect_logs_tool["arguments"]
        )
        assert any(argument["name"] == "tail_lines" for argument in collect_logs_tool["arguments"])
        list_projects_tool = next(
            item for item in bootstrap_text["tools"] if item["tool_name"] == "list_projects"
        )
        assert list_projects_tool["arguments"] == []
        assert all(
            item["tool_name"] != "analyze_daily_log_bundle" for item in bootstrap_text["tools"]
        )

    asyncio.run(run_test())


def test_tool_metadata_filters_tools_by_token_scopes() -> None:
    codex_token = AccessToken(
        token="codex-dev-token",
        client_id="codex-agent",
        scopes=[MCP_HEALTH_READ_SCOPE, CONTAINER_FILES_READ_SCOPE],
        claims={"sub": "codex-agent", "client_type": "codex", "project_key": "landingpage"},
    )

    allowed_tool_names = [
        entry["tool_name"] for entry in get_allowed_workflow_tool_metadata(codex_token)
    ]

    assert "get_mcp_health_check" in allowed_tool_names
    assert "read_container_file" not in allowed_tool_names
    assert "analyze_daily_log_bundle" not in allowed_tool_names
    assert "get_mcp_service_status" not in allowed_tool_names


def test_tool_auth_check_uses_token_scopes_per_request() -> None:
    workflow_check = require_scopes(WORKFLOW_BOOTSTRAP_SCOPE)
    workflow_token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=[WORKFLOW_BOOTSTRAP_SCOPE],
        claims={
            "sub": "workflow-agent",
            "client_type": "workflow_agent",
            "project_key": "landingpage",
        },
    )
    codex_token = AccessToken(
        token="codex-dev-token",
        client_id="codex-agent",
        scopes=[MCP_HEALTH_READ_SCOPE],
        claims={"sub": "codex-agent", "client_type": "codex", "project_key": "landingpage"},
    )

    from fastmcp.server.auth.authorization import AuthContext as FastMCPAuthContext

    component = FastMCPComponent(name="dummy-component")

    assert workflow_check(FastMCPAuthContext(token=workflow_token, component=component)) is True
    assert workflow_check(FastMCPAuthContext(token=codex_token, component=component)) is False


def test_workflow_bootstrap_uses_skill_resource_uris() -> None:
    workflow_token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=[
            WORKFLOW_BOOTSTRAP_SCOPE,
            LOGS_COLLECT_SCOPE,
            PROJECTS_READ_SCOPE,
            MCP_STATUS_READ_SCOPE,
            MCP_HEALTH_READ_SCOPE,
        ],
        claims={
            "sub": "workflow-agent",
            "client_type": "workflow_agent",
            "project_key": "landingpage",
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
