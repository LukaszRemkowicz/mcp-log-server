"""Tests for project-level developer shell scripts."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_mock import MockerFixture


def test_developer_shell_bootstraps_project_namespace(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    async def fake_initialize_database(config: dict[str, Any]) -> None:
        calls.append(f"init:{config['connections']['default']}")

    async def fake_close_database() -> None:
        calls.append("close")

    from scripts import shell

    mocker.patch("scripts.shell.initialize_database", fake_initialize_database)
    mocker.patch("scripts.shell.close_database", fake_close_database)

    result = shell.run_shell(start_repl=False)

    output = capsys.readouterr().out
    assert result == 0
    assert calls == [f"init:{shell.TORTOISE_ORM['connections']['default']}", "close"]
    assert "Preloaded imports:" in output
    assert (
        "from database.models import AgentCall, CollectLogs, CollectLogsSource, ProjectManifest"
        in output
    )
    assert "from database.types import AgentCallEvent, CollectLogsSourceStatus" in output
    assert "from database.services.project_manifests import ProjectManifestService" in output
