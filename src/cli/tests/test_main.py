"""Tests for the root CLI bridge."""

from __future__ import annotations

import sys

import pytest
from pytest_mock import MockerFixture

from cli import main as cli_main


def test_main_dry_run_prints_bridged_command(
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        sys,
        "argv",
        ["command", "--dry-run", "generate-dev-jwt"],
    )
    mocker.patch("cli.main.should_bridge_to_compose", return_value=True)
    mocker.patch("cli.main.get_current_environment", return_value="local")
    mocker.patch(
        "cli.main.build_compose_command",
        return_value=[
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "run",
            "--rm",
            "app",
            "python",
            "-m",
            "cli.main",
            "generate-dev-jwt",
        ],
    )
    run_compose_command = mocker.patch("cli.main.run_compose_command")

    with pytest.raises(SystemExit) as exit_info:
        cli_main.main()

    assert exit_info.value.code == 0
    expected_output = (
        "docker compose -f docker-compose.yml run --rm app python -m cli.main generate-dev-jwt\n"
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
        ["command", "generate-dev-jwt", "--dry-run"],
    )
    mocker.patch("cli.main.should_bridge_to_compose", return_value=True)
    mocker.patch("cli.main.get_current_environment", return_value="local")
    build_compose_command = mocker.patch(
        "cli.main.build_compose_command",
        return_value=["docker", "compose", "run", "--rm", "app", "true"],
    )

    with pytest.raises(SystemExit) as exit_info:
        cli_main.main()

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == "docker compose run --rm app true\n"
    build_compose_command.assert_called_once_with(
        "local",
        ["python", "-m", "cli.main", "generate-dev-jwt"],
    )


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
