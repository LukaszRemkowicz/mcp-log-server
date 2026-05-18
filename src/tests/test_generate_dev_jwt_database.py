"""Database-backed JWT generation tests."""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode
from typing import Any

import pytest

from core.types import LogWorkspace
from database.models import McpCaller
from scripts.commands.generate_dev_jwt import build_example_token_response


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    encoded_payload = token.split(".")[1]
    padded_payload = encoded_payload + "=" * (-len(encoded_payload) % 4)
    return json.loads(urlsafe_b64decode(padded_payload))


@pytest.mark.anyio
async def test_generate_dev_jwt_uses_database_callers_for_claims(db: None) -> None:  # noqa: ARG001
    await McpCaller.all().delete()
    await McpCaller.objects.create(
        client_id="workflow-prod",
        client_type="workflow_agent_prod",
        workspace=LogWorkspace.WORKFLOW,
        allowed_projects=["landingpage", "shop"],
    )
    await McpCaller.objects.create(
        client_id="codex-prod",
        client_type="codex_prod",
        workspace=LogWorkspace.SESSION,
        allowed_projects=["all"],
    )

    payload = await build_example_token_response()
    workflow_claims = _decode_jwt_payload(payload["workflow_agent"])
    codex_claims = _decode_jwt_payload(payload["codex_agent"])

    assert workflow_claims["sub"] == "workflow-prod"
    assert workflow_claims["client_id"] == "workflow-prod"
    assert workflow_claims["client_type"] == "workflow_agent_prod"
    assert workflow_claims["allowed_projects"] == ["landingpage", "shop"]
    assert codex_claims["sub"] == "codex-prod"
    assert codex_claims["client_id"] == "codex-prod"
    assert codex_claims["client_type"] == "codex_prod"
    assert codex_claims["allowed_projects"] == ["all"]


@pytest.mark.anyio
async def test_generate_dev_jwt_fails_when_expected_caller_row_is_missing(
    db: None,  # noqa: ARG001
) -> None:
    await McpCaller.all().delete()

    with pytest.raises(RuntimeError, match="Missing McpCaller row for workspace 'workflow'"):
        await build_example_token_response()
