"""Tests for local development JWT generation commands."""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode
from pathlib import Path
from typing import Any

from click.utils import strip_ansi
from typer.testing import CliRunner

from scripts.main import app

runner = CliRunner()


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    encoded_payload = token.split(".")[1]
    padded_payload = encoded_payload + "=" * (-len(encoded_payload) % 4)
    return json.loads(urlsafe_b64decode(padded_payload))


def test_generate_dev_jwt_command_prints_example_tokens() -> None:
    result = runner.invoke(app, ["generate-dev-jwt"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert set(payload) == {"workflow_agent", "codex_agent", "created_at", "updated_at"}
    assert payload["workflow_agent"]
    assert payload["codex_agent"]


def test_generate_dev_jwt_command_writes_tokens_to_output_file() -> None:
    with runner.isolated_filesystem():
        output_file = Path(".agent/DEV_JWT_TOKENS.json")

        result = runner.invoke(app, ["generate-dev-jwt", "--output-file", str(output_file)])

        assert result.exit_code == 0
        assert result.output == ""
        payload = json.loads(output_file.read_text())
        assert set(payload) == {"workflow_agent", "codex_agent", "created_at", "updated_at"}
        assert payload["workflow_agent"]
        assert payload["codex_agent"]


def test_generate_dev_jwt_help_describes_command_purpose() -> None:
    result = runner.invoke(app, ["generate-dev-jwt", "--help"])
    output = strip_ansi(result.output)

    assert result.exit_code == 0
    assert "Generate signed local development JWTs for MCP clients." in output
    assert "--output-file" in output
    assert "--workflow-client-id" in output
    assert "workflow-agent" in output
    assert "--codex-client-type" in output
    assert "codex" in output


def test_generate_dev_jwt_command_accepts_client_claim_overrides() -> None:
    result = runner.invoke(
        app,
        [
            "generate-dev-jwt",
            "--workflow-client-id",
            "workflow-local",
            "--workflow-client-type",
            "workflow_test",
            "--codex-client-id",
            "codex-local",
            "--codex-client-type",
            "codex_test",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    workflow_claims = _decode_jwt_payload(payload["workflow_agent"])
    codex_claims = _decode_jwt_payload(payload["codex_agent"])

    assert workflow_claims["client_id"] == "workflow-local"
    assert workflow_claims["client_type"] == "workflow_test"
    assert codex_claims["client_id"] == "codex-local"
    assert codex_claims["client_type"] == "codex_test"
