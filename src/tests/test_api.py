from __future__ import annotations

from collections.abc import Callable

import pytest

from auth.scopes import (
    MCP_HEALTH_READ_SCOPE,
    MCP_STATUS_READ_SCOPE,
    WORKFLOW_BOOTSTRAP_SCOPE,
    WORKFLOW_SKILLS_READ_SCOPE,
)
from tests.conftest import JsonRpcClient


@pytest.mark.parametrize(
    ("subject", "scopes", "client_id", "expected_present", "expected_absent"),
    [
        (
            "workflow-agent",
            [
                WORKFLOW_BOOTSTRAP_SCOPE,
                WORKFLOW_SKILLS_READ_SCOPE,
                MCP_STATUS_READ_SCOPE,
                MCP_HEALTH_READ_SCOPE,
            ],
            "workflow-agent",
            {
                "analyze_daily_log_bundle",
                "get_mcp_service_status",
                "get_mcp_health_check",
            },
            set(),
        ),
        (
            "codex-agent",
            [MCP_HEALTH_READ_SCOPE],
            "codex-agent",
            {"get_mcp_health_check"},
            {
                "analyze_daily_log_bundle",
                "get_mcp_service_status",
            },
        ),
    ],
)
def test_tools_list_filters_visible_tools_per_jwt(
    create_test_jwt_token: Callable[[str, list[str], str], str],
    jsonrpc: JsonRpcClient,
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
    jsonrpc: JsonRpcClient,
) -> None:
    workflow_token = create_test_jwt_token(
        "workflow-agent",
        [
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
    assert any(item["tool_name"] == "get_mcp_service_status" for item in payload["tools"])
    assert any(item["tool_name"] == "get_mcp_health_check" for item in payload["tools"])


def test_workflow_skill_resource_read_api_returns_skill_contents(
    create_test_jwt_token: Callable[[str, list[str], str], str],
    jsonrpc: JsonRpcClient,
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
    jsonrpc: JsonRpcClient,
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


def test_resource_templates_list_is_empty_for_fixed_skill_inventory(
    create_test_jwt_token: Callable[[str, list[str], str], str],
    jsonrpc: JsonRpcClient,
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
    assert templates == []


def test_codex_cannot_access_workflow_components(
    create_test_jwt_token: Callable[[str, list[str], str], str],
    jsonrpc: JsonRpcClient,
) -> None:
    codex_token = create_test_jwt_token(
        "codex-agent",
        [MCP_HEALTH_READ_SCOPE],
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
    jsonrpc: JsonRpcClient,
) -> None:
    response = jsonrpc.post(
        token=None,
        data={"jsonrpc": "2.0", "id": "7", "method": "tools/list", "params": {}},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"
