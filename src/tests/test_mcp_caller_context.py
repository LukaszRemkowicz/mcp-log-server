from __future__ import annotations

from types import SimpleNamespace

import mcp.types as mt
import pytest
from fastmcp.server.auth import AccessToken
from fastmcp.tools.base import ToolResult

from auth.mcp_caller_context import AuthenticatedMcpCaller
from core.types import LogWorkspace
from decorators import project_authorized_tool


def _token_with_claims(claims: dict[str, object]) -> AccessToken:
    return AccessToken(
        token="test-token",
        client_id="codex-client",
        scopes=["logs.collect"],
        claims={
            "sub": "codex-subject",
            "client_id": "codex-client",
            "client_type": "codex",
            **claims,
        },
    )


@pytest.mark.anyio
async def test_project_authorized_tool_uses_request_caller_projects(mocker) -> None:
    """Verify request caller, not JWT project claims, authorizes project tools."""

    @project_authorized_tool
    async def sample_tool(
        project_name: str,
        access_token: AccessToken | None = None,
    ) -> ToolResult:
        return ToolResult(content=[], structured_content={"project_name": project_name})

    token = _token_with_claims({})
    caller = AuthenticatedMcpCaller(
        caller_id=1,
        client_id="codex-client",
        client_type="codex",
        workspace=LogWorkspace.WORKFLOW,
        allowed_projects=frozenset({"landingpage"}),
    )
    request = SimpleNamespace(state=SimpleNamespace(caller=caller))
    mocker.patch("decorators.get_http_request", return_value=request)

    result = await sample_tool(project_name=" landingpage ", access_token=token)

    assert result.structured_content == {"project_name": "landingpage"}
    assert "allowed_projects" not in token.claims


@pytest.mark.anyio
async def test_project_authorized_tool_rejects_jwt_project_claim_override(mocker) -> None:
    """Verify JWT project claims cannot grant access beyond the DB caller row."""

    @project_authorized_tool
    async def sample_tool(
        project_name: str,
        access_token: AccessToken | None = None,
    ) -> ToolResult:
        return ToolResult(content=[], structured_content={"project_name": project_name})

    token = _token_with_claims({"projects_access": "all"})
    caller = AuthenticatedMcpCaller(
        caller_id=1,
        client_id="codex-client",
        client_type="codex",
        workspace=LogWorkspace.WORKFLOW,
        allowed_projects=frozenset({"landingpage"}),
    )
    request = SimpleNamespace(state=SimpleNamespace(caller=caller))
    mocker.patch("decorators.get_http_request", return_value=request)

    result = await sample_tool(project_name="shop", access_token=token)
    mcp_result = result.to_mcp_result()

    assert isinstance(mcp_result, mt.CallToolResult)
    assert mcp_result.isError is True
    assert mcp_result.structuredContent is not None
    assert mcp_result.structuredContent["error_code"] == "project_access_mismatch"


@pytest.mark.anyio
async def test_project_authorized_tool_rejects_empty_request_caller_projects(mocker) -> None:
    """Verify empty DB project access still rejects project-bound tools."""

    @project_authorized_tool
    async def sample_tool(project_name: str) -> ToolResult:
        return ToolResult(content=[], structured_content={"project_name": project_name})

    caller = AuthenticatedMcpCaller(
        caller_id=1,
        client_id="codex-client",
        client_type="codex",
        workspace=LogWorkspace.WORKFLOW,
        allowed_projects=frozenset(),
    )
    request = SimpleNamespace(state=SimpleNamespace(caller=caller))
    mocker.patch("decorators.get_http_request", return_value=request)

    result = await sample_tool(project_name="landingpage")
    mcp_result = result.to_mcp_result()

    assert isinstance(mcp_result, mt.CallToolResult)
    assert mcp_result.isError is True
    assert mcp_result.structuredContent is not None
    assert mcp_result.structuredContent["error_code"] == "project_access_mismatch"
