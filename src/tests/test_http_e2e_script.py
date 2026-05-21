"""Tests for the HTTP MCP end-to-end shell script."""

from __future__ import annotations

from pathlib import Path


def test_http_e2e_generates_jwts_after_test_callers_exist() -> None:
    """DB-backed JWT generation must run after the test DB is seeded."""

    script = Path(__file__).resolve().parents[2] / "infra/scripts/run_http_e2e.sh"
    content = script.read_text(encoding="utf-8")

    ensure_position = content.index("uv run python -m database.ensure_test_database")
    migration_position = content.index("uv run migrate >/dev/null")
    seed_position = content.index('INSERT INTO "mcp_callers"')
    jwt_position = content.index("uv run commands generate-dev-jwt")

    assert ensure_position < migration_position < seed_position < jwt_position


def test_http_e2e_collect_logs_requests_omit_workspace_argument() -> None:
    """HTTP E2E should let middleware inject caller-owned collect_logs workspace."""

    script = Path(__file__).resolve().parents[2] / "infra/scripts/run_http_e2e.sh"
    content = script.read_text(encoding="utf-8")

    assert '"workspace":"workflow"' not in content
    assert '"workspace":"session"' not in content
