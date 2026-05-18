from __future__ import annotations

import pytest
from fastmcp import Client
from mcp.shared.exceptions import McpError

from app import create_application


@pytest.mark.anyio
async def test_in_memory_client_hides_protected_components_without_auth() -> None:
    app = create_application(auth_provider=None)

    async with Client(app) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        prompts = await client.list_prompts()
        tool_result = await client.call_tool_mcp("analyze_daily_log_bundle", {})

        assert tools == []
        assert resources == []
        assert prompts == []
        assert tool_result.isError is True
        assert "Authenticated access token is required" in tool_result.content[0].text

        with pytest.raises(McpError, match="Unknown resource"):
            await client.read_resource("skill://workflow/severity_guide")
