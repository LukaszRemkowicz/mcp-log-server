"""Tests for the root CLI bridge."""

from __future__ import annotations

import sys

import pytest
from pytest_mock import MockerFixture

from cli import main as cli_main
from cli.utils import ComposeRunPolicy


def test_main_dry_run_prints_bridged_command(
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        sys,
        "argv",
        ["command", "--dry-run", "upload-project-manifest", "--all"],
    )
    mocker.patch("cli.main.should_bridge_to_compose", return_value=True)
    mocker.patch("cli.main.get_current_environment", return_value="local")
    mocker.patch(
        "cli.main.resolve_compose_run_policy",
        return_value=ComposeRunPolicy(preflight_services=("db",), no_deps=True),
    )
    mocker.patch(
        "cli.main.build_compose_up_command",
        return_value=["docker", "compose", "-f", "docker-compose.yml", "up", "-d", "db"],
    )
    mocker.patch(
        "cli.main.build_compose_command",
        return_value=[
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "run",
            "--no-deps",
            "--rm",
            "app",
            "python",
            "-m",
            "cli.main",
            "upload-project-manifest",
            "--all",
        ],
    )
    run_compose_command = mocker.patch("cli.main.run_compose_command")

    with pytest.raises(SystemExit) as exit_info:
        cli_main.main()

    assert exit_info.value.code == 0
    expected_output = (
        "docker compose -f docker-compose.yml up -d db\n"
        "docker compose -f docker-compose.yml run --no-deps --rm app "
        "python -m cli.main upload-project-manifest --all\n"
    )
    assert capsys.readouterr().out == expected_output
    run_compose_command.assert_not_called()


def test_main_dry_run_flag_can_follow_subcommand(
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        sys,
        "argv",
        ["command", "upload-project-manifest", "--all", "--dry-run"],
    )
    mocker.patch("cli.main.should_bridge_to_compose", return_value=True)
    mocker.patch("cli.main.get_current_environment", return_value="local")
    mocker.patch(
        "cli.main.resolve_compose_run_policy",
        return_value=ComposeRunPolicy(preflight_services=("db",), no_deps=True),
    )
    mocker.patch(
        "cli.main.build_compose_up_command",
        return_value=["docker", "compose", "up", "-d", "db"],
    )
    build_compose_command = mocker.patch(
        "cli.main.build_compose_command",
        return_value=["docker", "compose", "run", "--no-deps", "--rm", "app", "true"],
    )

    with pytest.raises(SystemExit) as exit_info:
        cli_main.main()

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == (
        "docker compose up -d db\ndocker compose run --no-deps --rm app true\n"
    )
    build_compose_command.assert_called_once_with(
        "local",
        ["python", "-m", "cli.main", "upload-project-manifest", "--all"],
        no_deps=True,
    )


def test_generate_dev_jwt_dry_run_only_prints_db_bootstrap(
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(sys, "argv", ["command", "generate-dev-jwt", "--dry-run"])
    mocker.patch("cli.main.should_bridge_to_compose", return_value=True)
    mocker.patch("cli.main.get_current_environment", return_value="local")
    mocker.patch(
        "cli.main.build_compose_up_command",
        return_value=["docker", "compose", "-f", "docker-compose.yml", "up", "-d", "db"],
    )
    app = mocker.patch("cli.main.app")
    run_compose_command = mocker.patch("cli.main.run_compose_command")

    with pytest.raises(SystemExit) as exit_info:
        cli_main.main()

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == "docker compose -f docker-compose.yml up -d db\n"
    app.assert_not_called()
    run_compose_command.assert_not_called()


def test_generate_dev_jwt_runs_locally_after_starting_db(mocker: MockerFixture) -> None:
    mocker.patch.object(sys, "argv", ["command", "generate-dev-jwt"])
    mocker.patch("cli.main.should_bridge_to_compose", return_value=True)
    mocker.patch("cli.main.get_current_environment", return_value="local")
    ensure_started = mocker.patch("cli.main.ensure_compose_services_started", return_value=0)
    app = mocker.patch("cli.main.app")
    run_compose_command = mocker.patch("cli.main.run_compose_command")

    cli_main.main()

    ensure_started.assert_called_once_with("local", ("db",))
    app.assert_called_once_with()
    run_compose_command.assert_not_called()


def test_main_help_does_not_bridge_to_compose(
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(sys, "argv", ["command", "--help"])
    mocker.patch("cli.main.should_bridge_to_compose", return_value=True)
    run_compose_command = mocker.patch("cli.main.run_compose_command")

    with pytest.raises(SystemExit) as exit_info:
        cli_main.main()

    assert exit_info.value.code == 0
    assert "Project maintenance commands for mcp-log-server" in capsys.readouterr().out
    run_compose_command.assert_not_called()


def test_subcommand_help_does_not_bridge_to_compose(
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(sys, "argv", ["command", "upload-project-manifest", "--help"])
    mocker.patch("cli.main.should_bridge_to_compose", return_value=True)
    run_compose_command = mocker.patch("cli.main.run_compose_command")

    with pytest.raises(SystemExit) as exit_info:
        cli_main.main()

    assert exit_info.value.code == 0
    assert "Upload one or all configured project manifests" in capsys.readouterr().out
    run_compose_command.assert_not_called()
