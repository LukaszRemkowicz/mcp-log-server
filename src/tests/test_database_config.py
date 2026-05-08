"""Tests for database ORM configuration."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from pytest_mock import MockerFixture

import database.cli as database_cli
import database.config as database_config
from database.models import AgentCall, ProjectManifest
from tests.conftest import override_settings


def test_tortoise_config_uses_environment_backed_db() -> None:
    with override_settings(
        DATABASE_HOST="db",
        DATABASE_PORT=5432,
        DATABASE_NAME="mcp_log_server",
        DATABASE_USER="mcp_log_server",
        DATABASE_PASSWORD="local-secret",
    ):
        config = database_config.build_tortoise_config()

    assert config["connections"]["default"] == (
        "postgres://mcp_log_server:local-secret@db:5432/mcp_log_server"
    )
    assert config["apps"]["models"]["models"] == [
        "database.models",
        "aerich.models",
    ]
    assert config["apps"]["models"]["default_connection"] == "default"
    assert config["apps"]["models"]["migrations"] == "migrations/models"


def test_makemigrations_runs_aerich_migrate(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[list[str], bool, bool, dict[str, str]]] = []

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        check: bool,
        env: dict[str, str],
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, capture_output, check, env))
        return subprocess.CompletedProcess(args, 0, stdout="generated\n", stderr="")

    mocker.patch("database.cli.subprocess.run", fake_run)

    result = database_cli._run_makemigrations(["--name", "add_agent_calls"])

    assert result == 0
    assert calls[0][0] == ["aerich", "migrate", "--name", "add_agent_calls"]
    assert calls[0][1] is True
    assert calls[0][2] is True
    assert calls[0][3]["DATABASE_HOST"] == "127.0.0.1"
    assert calls[0][3]["DATABASE_PORT"] == "5437"
    assert capsys.readouterr().out == "generated\n"


def test_makemigrations_initializes_aerich_when_required(
    mocker: MockerFixture,
) -> None:
    calls: list[tuple[list[str], bool, bool, dict[str, str]]] = []

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        check: bool,
        env: dict[str, str],
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, capture_output, check, env))
        if args == ["aerich", "migrate"]:
            raise subprocess.CalledProcessError(
                1,
                args,
                output="",
                stderr=f"Error: {database_cli.INIT_DB_REQUIRED_MESSAGES[0]}\n",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    mocker.patch("database.cli.subprocess.run", fake_run)

    result = database_cli._run_makemigrations([])

    assert result == 0
    assert [(args, capture_output, check) for args, capture_output, check, _env in calls] == [
        (["aerich", "migrate"], True, True),
        (["aerich", "init-db"], False, False),
    ]
    assert calls[1][3]["DATABASE_HOST"] == "127.0.0.1"
    assert calls[1][3]["DATABASE_PORT"] == "5437"


def test_makemigrations_initializes_aerich_when_maybe_required(
    mocker: MockerFixture,
) -> None:
    calls: list[list[str]] = []

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        check: bool,
        env: dict[str, str],
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args == ["aerich", "migrate"]:
            raise subprocess.CalledProcessError(
                1,
                args,
                output="",
                stderr=f"Error: {database_cli.INIT_DB_REQUIRED_MESSAGES[1]}\n",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    mocker.patch("database.cli.subprocess.run", fake_run)

    result = database_cli._run_makemigrations([])

    assert result == 0
    assert calls == [["aerich", "migrate"], ["aerich", "init-db"]]


def test_migration_commands_preserve_explicit_database_env(
    mocker: MockerFixture,
) -> None:
    captured_envs: list[dict[str, str]] = []

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        check: bool,
        env: dict[str, str],
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        captured_envs.append(env)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    mocker.patch("database.cli.subprocess.run", fake_run)
    mocker.patch.dict(
        "database.cli.os.environ",
        {"DATABASE_HOST": "db", "DATABASE_PORT": "5432"},
    )

    result = database_cli._run_makemigrations([])

    assert result == 0
    assert captured_envs[0]["DATABASE_HOST"] == "db"
    assert captured_envs[0]["DATABASE_PORT"] == "5432"


def test_database_models_are_importable_from_dedicated_module() -> None:
    assert AgentCall.Meta.table == "agent_calls"
    assert set(AgentCall._meta.fields_map) == {
        "id",
        "created_at",
        "session_id",
        "session_ended",
        "workspace",
        "event",
        "subject",
        "client_id",
        "client_type",
        "tool_name",
        "uri",
        "duration_ms",
        "success",
        "error_code",
        "project_name",
        "source_keys",
        "arguments",
        "result_summary",
    }
    assert (
        AgentCall._meta.fields_map["session_id"].description
        == "MCP-generated UUID shared by all rows that belong to one agent session."
    )
    assert (
        AgentCall._meta.fields_map["workspace"].description
        == "Agent workspace for the call, currently either 'session' or 'workflow'."
    )
    assert (
        AgentCall._meta.fields_map["uri"].description
        == "MCP resource URI when event records a resource read, such as a workflow skill URI."
    )

    assert ProjectManifest.Meta.table == "project_manifests"
    assert set(ProjectManifest._meta.fields_map) == {
        "id",
        "created_at",
        "updated_at",
        "project_key",
        "project_summary",
        "static_asset_paths",
        "static_asset_extensions",
        "sources",
    }
    assert (
        ProjectManifest._meta.fields_map["project_key"].description
        == "Stable project key from the manifest, for example 'landingpage'."
    )
    assert (
        ProjectManifest._meta.fields_map["sources"].description
        == "List of source definitions with the same shape as Manifest.sources."
    )


@pytest.mark.anyio
async def test_database_lifecycle_initializes_and_closes_tortoise(
    mocker: MockerFixture,
) -> None:
    calls: list[tuple[str, Any]] = []

    async def fake_init(**tortoise_kwargs: dict[str, object]) -> None:
        calls.append(("init", tortoise_kwargs["config"]))

    async def fake_close_connections() -> None:
        calls.append(("close", None))

    mocker.patch("database.lifecycle.Tortoise.init", fake_init)
    mocker.patch(
        "database.lifecycle.Tortoise.close_connections",
        fake_close_connections,
    )

    from database.lifecycle import close_database, initialize_database

    apps_config = {
        "models": {
            "models": ["database.models", "aerich.models"],
            "default_connection": "default",
            "migrations": "migrations/models",
        },
    }
    config = {
        "connections": {
            "default": "postgres://mcp_log_server:mcp-log-server-local-password@db:5432/mcp_log_server",
        },
        "apps": apps_config,
    }
    await initialize_database(config)
    await close_database()

    assert calls == [
        (
            "init",
            {
                "connections": {
                    "default": "postgres://mcp_log_server:mcp-log-server-local-password@db:5432/mcp_log_server",
                },
                "apps": apps_config,
            },
        ),
        ("close", None),
    ]


@pytest.mark.anyio
async def test_database_lifespan_wraps_initialization_and_shutdown(
    mocker: MockerFixture,
) -> None:
    calls: list[str] = []

    async def fake_initialize_database(config: dict[str, Any]) -> None:
        calls.append(f"init:{config['connections']['default']}")

    async def fake_close_database() -> None:
        calls.append("close")

    mocker.patch(
        "database.lifecycle.initialize_database",
        fake_initialize_database,
    )
    mocker.patch("database.lifecycle.close_database", fake_close_database)

    from database.lifecycle import database_lifespan

    async with database_lifespan(object()):
        calls.append("inside")

    assert calls == [
        "init:postgres://mcp_log_server:mcp-log-server-local-password@127.0.0.1:5432/mcp_log_server",
        "inside",
        "close",
    ]
