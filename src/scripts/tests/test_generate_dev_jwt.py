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


async def _fake_token_response(
    _settings: object,
    *,
    exp_time_hours: int | None = None,
) -> dict[str, str]:
    return {
        "workflow_agent": "workflow-token",
        "codex_agent": "codex-token",
        "created_at": "2026-05-18T00:00:00Z",
        "updated_at": "2026-05-18T00:00:00Z",
    }


def test_generate_dev_jwt_command_prints_example_tokens(mocker) -> None:
    mocker.patch(
        "scripts.commands.generate_dev_jwt._build_example_token_response_with_database",
        _fake_token_response,
    )

    result = runner.invoke(app, ["generate-dev-jwt"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert set(payload) == {"workflow_agent", "codex_agent", "created_at", "updated_at"}
    assert payload["workflow_agent"]
    assert payload["codex_agent"]


def test_generate_dev_jwt_command_passes_custom_exp_time_hours(mocker) -> None:
    expected_payload = {
        "workflow_agent": "workflow-token",
        "codex_agent": "codex-token",
        "created_at": "2026-05-18T00:00:00Z",
        "updated_at": "2026-05-18T00:00:00Z",
    }
    build_response = mocker.AsyncMock(return_value=expected_payload)
    mocker.patch(
        "scripts.commands.generate_dev_jwt._build_example_token_response_with_database",
        build_response,
    )

    result = runner.invoke(app, ["generate-dev-jwt", "--exp-time", "720"])

    assert result.exit_code == 0
    assert json.loads(result.output) == expected_payload
    build_response.assert_awaited_once()
    assert build_response.await_args.kwargs == {"exp_time_hours": 720}


def test_generate_dev_jwt_command_writes_tokens_to_output_file(mocker) -> None:
    mocker.patch(
        "scripts.commands.generate_dev_jwt._build_example_token_response_with_database",
        _fake_token_response,
    )

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
    assert "--exp-time" in output
    assert "--workflow-client-id" not in output
    assert "--codex-client-type" not in output
