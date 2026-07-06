"""Tests for the developer shell script."""

from __future__ import annotations

import os
import subprocess
import sys
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

    from cli import shell

    mocker.patch("cli.shell.initialize_database", fake_initialize_database)
    mocker.patch("cli.shell.close_database", fake_close_database)

    result = shell.run_shell(start_repl=False)

    output = capsys.readouterr().out
    namespace = shell.build_shell_namespace()
    assert result == 0
    assert calls == [f"init:{shell.TORTOISE_ORM['connections']['default']}", "close"]
    assert "McpCaller" in namespace
    assert "Task" in namespace
    assert "collect_logs_task" in namespace
    assert "Preloaded imports:" in output
    assert (
        "from database.models import McpCaller, AgentCall, CollectLogs, "
        "CollectLogsSource, ProjectManifest, Task" in output
    )
    assert "from core.types import LogWorkspace" in output
    assert "from database.types import AgentCallEvent, CollectLogsSourceStatus" in output
    assert "TaskStatus, TaskType" in output
    assert "from database.services.project_manifests import ProjectManifestService" in output
    assert "from database.services.tasks import TaskService" in output
    assert "from tasks import task, collect_logs_task" in output


def test_developer_shell_reexecs_inside_running_prod_app_container(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    tmp_path,
) -> None:
    from cli import shell

    mocker.patch("cli.shell._running_inside_container", return_value=False)
    mocker.patch(
        "cli.shell._find_running_mcp_app_container",
        return_value="mcp-app-1",
    )
    execvp = mocker.patch("cli.shell.os.execvp")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))

    shell.reexec_inside_running_mcp_app_container_if_needed()

    execvp.assert_called_once_with(
        "docker",
        [
            "docker",
            "exec",
            "-it",
            "mcp-app-1",
            "uv",
            "run",
            "shell",
        ],
    )


def test_developer_shell_reexec_does_not_pass_saved_tag_to_container(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    tmp_path,
) -> None:
    from cli import shell

    state_dir = tmp_path / "prod"
    state_dir.mkdir()
    (state_dir / "current_tag").write_text("v0.1.2\n", encoding="utf-8")
    mocker.patch("cli.shell._running_inside_container", return_value=False)
    mocker.patch(
        "cli.shell._find_running_mcp_app_container",
        return_value="mcp-app-1",
    )
    execvp = mocker.patch("cli.shell.os.execvp")
    monkeypatch.setenv("STATE_DIR", str(state_dir))

    shell.reexec_inside_running_mcp_app_container_if_needed()

    execvp.assert_called_once_with(
        "docker",
        [
            "docker",
            "exec",
            "-it",
            "mcp-app-1",
            "uv",
            "run",
            "shell",
        ],
    )


def test_developer_shell_finds_prod_app_container_by_compose_labels(
    mocker: MockerFixture,
) -> None:
    from cli import shell

    run = mocker.patch(
        "cli.shell.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "other-app-1\tother-image:latest\tother-project\n"
                "runtime-generated-container-name\tprod-mcp-log-server:abc123\tmcp\n"
            ),
            stderr="",
        ),
    )

    container_name = shell._find_running_mcp_app_container()

    assert container_name == "runtime-generated-container-name"
    run.assert_called_once_with(
        [
            "docker",
            "ps",
            "--filter",
            "label=com.docker.compose.service=app",
            "--filter",
            "status=running",
            "--format",
            '{{.Names}}\t{{.Image}}\t{{.Label "com.docker.compose.project"}}',
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_developer_shell_honors_explicit_compose_project_name(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    from cli import shell

    monkeypatch.setenv("COMMANDS_COMPOSE_PROJECT_NAME", "custom-prod")
    monkeypatch.setenv("COMMANDS_APP_SERVICE", "api")
    run = mocker.patch(
        "cli.shell.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="custom-container\n",
            stderr="",
        ),
    )

    container_name = shell._find_running_mcp_app_container()

    assert container_name == "custom-container"
    run.assert_called_once_with(
        [
            "docker",
            "ps",
            "--filter",
            "label=com.docker.compose.project=custom-prod",
            "--filter",
            "label=com.docker.compose.service=api",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_developer_shell_falls_back_when_explicit_compose_project_is_stale(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    from cli import shell

    monkeypatch.setenv("COMMANDS_COMPOSE_PROJECT_NAME", "stale-project")
    run = mocker.patch(
        "cli.shell.subprocess.run",
        side_effect=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=("runtime-generated-container-name\tprod-mcp-log-server:abc123\tmcp\n"),
                stderr="",
            ),
        ],
    )

    container_name = shell._find_running_mcp_app_container()

    assert container_name == "runtime-generated-container-name"
    assert run.call_args_list[0].args[0] == [
        "docker",
        "ps",
        "--filter",
        "label=com.docker.compose.project=stale-project",
        "--filter",
        "label=com.docker.compose.service=app",
        "--filter",
        "status=running",
        "--format",
        "{{.Names}}",
    ]
    assert run.call_args_list[1].args[0] == [
        "docker",
        "ps",
        "--filter",
        "label=com.docker.compose.service=app",
        "--filter",
        "status=running",
        "--format",
        '{{.Names}}\t{{.Image}}\t{{.Label "com.docker.compose.project"}}',
    ]


def test_developer_shell_skips_container_reexec_when_already_inside_container(
    mocker: MockerFixture,
) -> None:
    from cli import shell

    mocker.patch("cli.shell._running_inside_container", return_value=True)
    find_container = mocker.patch("cli.shell._find_running_mcp_app_container")
    execvp = mocker.patch("cli.shell.os.execvp")

    shell.reexec_inside_running_mcp_app_container_if_needed()

    find_container.assert_not_called()
    execvp.assert_not_called()


def test_developer_shell_defaults_to_compose_host_database_port() -> None:
    env = os.environ.copy()
    env.pop("DATABASE_HOST", None)
    env.pop("DATABASE_PORT", None)
    env.pop("DATABASE_PORT_HOST", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            ("from cli import shell; print(shell.TORTOISE_ORM['connections']['default'])"),
        ],
        capture_output=True,
        check=True,
        env=env,
        text=True,
    )

    assert "@127.0.0.1:5437/mcp_log_server" in result.stdout


def test_developer_shell_suppresses_ipython_cross_loop_close_error(
    mocker: MockerFixture,
) -> None:
    from cli import shell

    close_database = mocker.AsyncMock(
        side_effect=RuntimeError("got Future <Future pending> attached to a different loop")
    )
    mocker.patch("cli.shell.close_database", close_database)

    shell.close_shell_database()

    close_database.assert_awaited_once()
