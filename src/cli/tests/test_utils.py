"""Tests for shared script utilities."""

from __future__ import annotations

from pathlib import Path

from pytest_mock import MockerFixture

from cli import utils


def test_state_dir_resolver_uses_configured_state_dir(tmp_path: Path) -> None:
    configured_state_dir = tmp_path / "custom-state"

    state_dir = utils.get_state_dir(
        "prod",
        project_dir=tmp_path / "project",
        env={"STATE_DIR": str(configured_state_dir)},
    )

    assert state_dir == configured_state_dir


def test_state_dir_resolver_uses_fixed_prod_state_root(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    mocker.patch("utils.state_dir._can_use_preferred_state_dir", return_value=False)

    state_dir = utils.get_state_dir("prod", project_dir=tmp_path, env={})

    assert state_dir == utils.DEFAULT_STATE_ROOT / "prod"


def test_state_dir_resolver_falls_back_to_project_state_for_local(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    mocker.patch("utils.state_dir._can_use_preferred_state_dir", return_value=False)

    resolved_state_dir = utils.get_state_dir("local", project_dir=tmp_path, env={})

    assert resolved_state_dir == tmp_path / ".agent" / "state" / "local"


def test_resolve_prod_tag_prefers_explicit_tag_env(tmp_path: Path) -> None:
    state_dir = tmp_path / "prod"
    state_dir.mkdir()
    (state_dir / "current_tag").write_text("v0.1.2\n", encoding="utf-8")

    tag = utils.resolve_prod_tag(
        required=True,
        env={"TAG": "v0.1.3", "STATE_DIR": str(state_dir)},
    )

    assert tag == "v0.1.3"


def test_resolve_prod_tag_uses_current_tag_file(tmp_path: Path) -> None:
    state_dir = tmp_path / "prod"
    state_dir.mkdir()
    (state_dir / "current_tag").write_text("v0.1.2\n", encoding="utf-8")

    tag = utils.resolve_prod_tag(
        required=True,
        env={"STATE_DIR": str(state_dir)},
    )

    assert tag == "v0.1.2"


def test_resolve_prod_tag_reads_cli_state_dir_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "prod"
    state_dir.mkdir()
    (state_dir / "current_tag").write_text("v0.1.2\n", encoding="utf-8")
    monkeypatch.delenv("TAG", raising=False)
    monkeypatch.setenv("STATE_DIR", str(state_dir))

    tag = utils.resolve_prod_tag(required=True)

    assert tag == "v0.1.2"
    assert "TAG" not in utils.os.environ


def test_build_compose_command_runs_local_app_service_command() -> None:
    command = utils.build_compose_command(
        "local",
        ["command", "upload-project-manifest", "--all"],
    )

    assert command == [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "run",
        "--rm",
        "app",
        "command",
        "upload-project-manifest",
        "--all",
    ]


def test_build_compose_command_runs_prod_app_service_command(
    mocker: MockerFixture,
) -> None:
    mocker.patch("cli.utils.resolve_prod_tag", return_value="v0.1.2")

    command = utils.build_compose_command(
        "prod",
        ["command", "upload-project-manifest", "--all"],
    )

    assert command == [
        "env",
        "TAG=v0.1.2",
        "docker",
        "compose",
        "-f",
        "docker-compose.prod.yml",
        "run",
        "--rm",
        "app",
        "command",
        "upload-project-manifest",
        "--all",
    ]


def test_build_compose_command_uses_explicit_prod_tag(monkeypatch) -> None:
    monkeypatch.setenv("TAG", "v0.1.4")

    command = utils.build_compose_command(
        "prod",
        ["python", "-m", "cli.main", "generate-dev-jwt", "--exp-time", "576"],
    )

    assert command == [
        "env",
        "TAG=v0.1.4",
        "docker",
        "compose",
        "-f",
        "docker-compose.prod.yml",
        "run",
        "--rm",
        "app",
        "python",
        "-m",
        "cli.main",
        "generate-dev-jwt",
        "--exp-time",
        "576",
    ]


def test_commands_compose_project_name_prefers_commands_env(
    monkeypatch,
) -> None:
    monkeypatch.setenv("COMMANDS_COMPOSE_PROJECT_NAME", "custom-commands")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "compose-project")

    assert utils.get_commands_compose_project_name() == "custom-commands"


def test_commands_compose_project_name_falls_back_to_compose_env(
    monkeypatch,
) -> None:
    monkeypatch.delenv("COMMANDS_COMPOSE_PROJECT_NAME", raising=False)
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "compose-project")

    assert utils.get_commands_compose_project_name() == "compose-project"


def test_commands_app_service_defaults_to_app(
    monkeypatch,
) -> None:
    monkeypatch.delenv("COMMANDS_APP_SERVICE", raising=False)

    assert utils.get_commands_app_service() == "app"


def test_shell_repl_flag_uses_cli_env(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_SHELL_EXIT_AFTER_BOOT", "1")

    assert utils.should_start_shell_repl() is False


def test_should_bridge_to_compose_requires_host(
    mocker: MockerFixture,
    monkeypatch,
) -> None:
    monkeypatch.delenv("COMMANDS_DISABLE_COMPOSE_BRIDGE", raising=False)
    mocker.patch("cli.utils.is_running_in_container", return_value=False)

    assert utils.should_bridge_to_compose() is True


def test_should_bridge_to_compose_skips_container(
    mocker: MockerFixture,
    monkeypatch,
) -> None:
    monkeypatch.delenv("COMMANDS_DISABLE_COMPOSE_BRIDGE", raising=False)
    mocker.patch("cli.utils.is_running_in_container", return_value=True)

    assert utils.should_bridge_to_compose() is False


def test_should_bridge_to_compose_respects_disable_env(
    monkeypatch,
    mocker: MockerFixture,
) -> None:
    monkeypatch.setenv("COMMANDS_DISABLE_COMPOSE_BRIDGE", "1")
    mocker.patch("cli.utils.is_running_in_container", return_value=False)

    assert utils.should_bridge_to_compose() is False
