"""Tests for database ORM configuration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from pytest_mock import MockerFixture

import cli.db as database_cli
import cli.test as test_cli
import database.config as database_config
import database.ensure_test_database as ensure_test_database_module
from auth.mcp_caller_model import get_mcp_caller_model
from core.types import LogWorkspace
from database.fields import FileField
from database.managers import CollectLogsManager
from database.models import (
    AgentCall,
    AgentSession,
    CollectLogs,
    CollectLogsSource,
    McpCaller,
    ProjectManifest,
)
from database.types import (
    AgentCallEvent,
    AgentSessionStatus,
    CollectLogsSourceStatus,
    LogSourceType,
    LogStream,
)
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

    mocker.patch("cli.db.subprocess.run", fake_run)
    mocker.patch.dict("cli.db.os.environ", {}, clear=True)

    result = database_cli._run_makemigrations(["--name", "add_agent_calls"])

    assert result == 0
    assert calls[0][0] == ["aerich", "migrate", "--name", "add_agent_calls"]
    assert calls[0][1] is True
    assert calls[0][2] is True
    assert calls[0][3]["DATABASE_HOST"] == "127.0.0.1"
    assert calls[0][3]["DATABASE_PORT"] == "5437"
    assert capsys.readouterr().out == "generated\n"


def test_makemigrations_normalizes_aerich_filename(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    migrations_dir = tmp_path / "migrations" / "models"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_init.py").write_text("init\n", encoding="utf-8")

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        check: bool,
        env: dict[str, str],
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        (migrations_dir / "2_20260517120000_remove_agent_call_redundant_fields.py").write_text(
            "upgrade\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    mocker.patch("cli.db.subprocess.run", fake_run)
    mocker.patch("cli.db.MIGRATIONS_DIR", migrations_dir)

    result = database_cli._run_makemigrations([])

    assert result == 0
    assert not (migrations_dir / "2_20260517120000_remove_agent_call_redundant_fields.py").exists()
    assert (migrations_dir / "002_remove_agent_call_redundant_fields.py").read_text(
        encoding="utf-8"
    ) == "upgrade\n"


def test_makemigrations_accepts_positional_suffix(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    migrations_dir = tmp_path / "migrations" / "models"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_init.py").write_text("init\n", encoding="utf-8")
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
        (migrations_dir / "2_20260517120000_update.py").write_text("upgrade\n", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    mocker.patch("cli.db.subprocess.run", fake_run)
    mocker.patch("cli.db.MIGRATIONS_DIR", migrations_dir)

    result = database_cli._run_makemigrations(["Remove agent call redundant fields"])

    assert result == 0
    assert calls == [["aerich", "migrate", "--name", "remove_agent_call_redundant_fields"]]
    assert (migrations_dir / "002_remove_agent_call_redundant_fields.py").read_text(
        encoding="utf-8"
    ) == "upgrade\n"


def test_makemigrations_slugifies_aerich_name_option(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    migrations_dir = tmp_path / "migrations" / "models"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001_init.py").write_text("init\n", encoding="utf-8")
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
        (migrations_dir / "2_20260517120000_update.py").write_text("upgrade\n", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    mocker.patch("cli.db.subprocess.run", fake_run)
    mocker.patch("cli.db.MIGRATIONS_DIR", migrations_dir)

    result = database_cli._run_makemigrations(["--name", "Remove agent call redundant fields"])

    assert result == 0
    assert calls == [["aerich", "migrate", "--name", "remove_agent_call_redundant_fields"]]
    assert (migrations_dir / "002_remove_agent_call_redundant_fields.py").read_text(
        encoding="utf-8"
    ) == "upgrade\n"


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

    mocker.patch("cli.db.subprocess.run", fake_run)
    mocker.patch.dict("cli.db.os.environ", {}, clear=True)

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

    mocker.patch("cli.db.subprocess.run", fake_run)

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

    mocker.patch("cli.db.subprocess.run", fake_run)
    mocker.patch.dict(
        "cli.db.os.environ",
        {"DATABASE_HOST": "db", "DATABASE_PORT": "5432"},
    )

    result = database_cli._run_makemigrations([])

    assert result == 0
    assert captured_envs[0]["DATABASE_HOST"] == "db"
    assert captured_envs[0]["DATABASE_PORT"] == "5432"


def test_test_command_runs_compose_test_container(mocker: MockerFixture) -> None:
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(
        args: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, env))
        return subprocess.CompletedProcess(args, 0)

    mocker.patch("cli.test.subprocess.run", fake_run)

    result = test_cli._run_test()

    assert result == 0
    assert len(calls) == 1
    command, env = calls[0]
    assert command == [
        "docker",
        "compose",
        "run",
        "--rm",
        "--build",
        "test",
    ]
    assert env is not None
    assert env["DATABASE_PORT_HOST"] == "0"


@pytest.mark.anyio
async def test_ensure_test_database_recreates_only_configured_test_database(
    mocker: MockerFixture,
) -> None:
    calls: list[tuple[str, object]] = []

    class FakeConnection:
        async def execute(self, query: str, *args: object) -> None:
            calls.append((query.strip(), args))

        async def close(self) -> None:
            calls.append(("close", ()))

    async def fake_connect(**kwargs: object) -> FakeConnection:
        calls.append(("connect", kwargs))
        return FakeConnection()

    mocker.patch("database.ensure_test_database.asyncpg.connect", fake_connect)

    with override_settings(
        DATABASE_HOST="db",
        DATABASE_PORT=5432,
        DATABASE_NAME="mcp_log_server_test",
        DATABASE_USER="mcp_log_server",
        DATABASE_PASSWORD="local-secret",
    ):
        await ensure_test_database_module.ensure_test_database()

    assert calls == [
        (
            "connect",
            {
                "host": "db",
                "port": 5432,
                "user": "mcp_log_server",
                "password": "local-secret",
                "database": "postgres",
            },
        ),
        (
            "SELECT pg_terminate_backend(pid)\n"
            "            FROM pg_stat_activity\n"
            "            WHERE datname = $1\n"
            "              AND pid <> pg_backend_pid()",
            ("mcp_log_server_test",),
        ),
        ('DROP DATABASE IF EXISTS "mcp_log_server_test"', ()),
        ('CREATE DATABASE "mcp_log_server_test"', ()),
        ("close", ()),
    ]


@pytest.mark.anyio
async def test_ensure_test_database_rejects_non_test_database() -> None:
    with override_settings(DATABASE_NAME="mcp_log_server"):
        with pytest.raises(RuntimeError, match="Refusing to reset a non-test database"):
            await ensure_test_database_module.ensure_test_database()


def test_database_models_are_importable_from_dedicated_module() -> None:
    assert AgentCall.Meta.table == "agent_calls"
    agent_call_fields = set(AgentCall._meta.fields_map)
    agent_call_fields.discard("session_id")
    agent_call_fields.discard("caller_id")
    assert agent_call_fields == {
        "id",
        "created_at",
        "session",
        "caller",
        "event",
        "tool_name",
        "uri",
        "duration_seconds",
        "success",
        "error_code",
        "project_name",
        "source_keys",
        "arguments",
    }
    assert (
        AgentCall._meta.fields_map["session"].description
        == "Session that owns this recorded MCP call row."
    )
    assert (
        AgentCall._meta.fields_map["caller"].description
        == "Allowed MCP caller that created this recorded MCP call row."
    )
    assert cast(Any, AgentCall._meta.fields_map["event"]).enum_type is AgentCallEvent
    assert AgentCall._meta.fields_map["uri"].description == (
        "MCP resource URI when event records a resource read, such as a workflow "
        "skill URI. Tool-call rows leave this empty."
    )
    assert (
        AgentCall._meta.fields_map["duration_seconds"].description
        == "Measured call duration in seconds, when timing is available."
    )
    assert AgentCall.objects is not None
    assert AgentCall.objects._model is AgentCall

    assert AgentSession.Meta.table == "agent_sessions"
    agent_session_fields = set(AgentSession._meta.fields_map)
    agent_session_fields.discard("collect_logs")
    agent_session_fields.discard("agent_calls")
    agent_session_fields.discard("caller_id")
    assert agent_session_fields == {
        "id",
        "created_at",
        "updated_at",
        "name",
        "caller",
        "status",
        "closed_at",
    }
    assert (
        AgentSession._meta.fields_map["name"].description
        == "Human-readable session name returned to agents as session_id."
    )
    assert (
        AgentSession._meta.fields_map["caller"].description
        == "Allowed MCP caller that owns this agent session."
    )
    assert cast(Any, AgentSession._meta.fields_map["status"]).enum_type is AgentSessionStatus
    assert AgentSession.objects is not None
    assert AgentSession.objects._model is AgentSession

    assert CollectLogs.Meta.table == "collect_logs"
    collect_logs_fields = set(CollectLogs._meta.fields_map)
    collect_logs_fields.discard("sources")
    collect_logs_fields.discard("session_id")
    assert collect_logs_fields == {
        "id",
        "created_at",
        "workspace",
        "session",
        "project_name",
        "collected_at",
        "snapshot_dir",
        "archive_name",
        "is_latest",
        "requested_source_keys",
        "resolved_source_keys",
        "unknown_requested_source_keys",
        "requested_since",
        "requested_until",
        "warnings",
        "retry_tips",
    }
    assert cast(Any, CollectLogs._meta.fields_map["workspace"]).enum_type is LogWorkspace
    assert (
        CollectLogs._meta.fields_map["session"].description
        == "Session that owns this collected log artifact."
    )
    assert (
        CollectLogs._meta.fields_map["snapshot_dir"].description
        == "Persisted snapshot directory path under the logs root."
    )
    assert (
        CollectLogs._meta.fields_map["archive_name"].description
        == "Workflow archive name when this workflow artifact is archived."
    )
    assert CollectLogs.objects is not None
    assert CollectLogs.objects._model is CollectLogs
    assert isinstance(CollectLogs.objects, CollectLogsManager)

    assert CollectLogsSource.Meta.table == "collect_logs_sources"
    collect_logs_source_fields = set(CollectLogsSource._meta.fields_map)
    collect_logs_source_fields.discard("collect_logs_id")
    assert collect_logs_source_fields == {
        "id",
        "created_at",
        "collect_logs",
        "source_key",
        "source_type",
        "target",
        "description",
        "stream",
        "parser_type",
        "normalization_profile",
        "default_noise_profile",
        "status",
        "file",
        "line_count",
        "error",
        "retry_tips",
    }
    assert cast(Any, CollectLogsSource._meta.fields_map["source_type"]).enum_type is LogSourceType
    assert cast(Any, CollectLogsSource._meta.fields_map["stream"]).enum_type is LogStream
    assert (
        cast(Any, CollectLogsSource._meta.fields_map["status"]).enum_type is CollectLogsSourceStatus
    )
    assert (
        CollectLogsSource._meta.fields_map["collect_logs"].description
        == "Parent collect_logs artifact this source file belongs to."
    )
    assert CollectLogsSource._meta.fields_map["file"].description == (
        "Logs-root-relative source file path, for example "
        "sessions/<session_id>/<project_name>/<source>.log or "
        "workflow/<project_name>/latest/<source>.log."
    )
    assert isinstance(CollectLogsSource._meta.fields_map["file"], FileField)
    assert CollectLogsSource.objects is not None
    assert CollectLogsSource.objects._model is CollectLogsSource

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
    assert ProjectManifest.objects is not None
    assert ProjectManifest.objects._model is ProjectManifest
    assert AgentCall.objects is not ProjectManifest.objects
    assert CollectLogs.objects is not CollectLogsSource.objects

    assert McpCaller.Meta.table == "mcp_callers"
    assert get_mcp_caller_model() is McpCaller
    mcp_caller_fields = set(McpCaller._meta.fields_map)
    mcp_caller_fields.discard("agent_calls")
    mcp_caller_fields.discard("agent_sessions")
    assert mcp_caller_fields == {
        "id",
        "created_at",
        "updated_at",
        "client_id",
        "client_type",
        "workspace",
        "allowed_projects",
    }
    assert (
        McpCaller._meta.fields_map["client_id"].description
        == "Stable client_id claim allowed to call MCP tools."
    )
    assert (
        McpCaller._meta.fields_map["client_type"].description
        == "Stable client_type claim allowed for this MCP client id."
    )
    assert (
        McpCaller._meta.fields_map["workspace"].description
        == "MCP workspace this caller is allowed to use."
    )
    assert (
        McpCaller._meta.fields_map["allowed_projects"].description
        == "Project names this MCP caller row is allowed to access."
    )
    assert McpCaller.objects is not AgentCall.objects


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

    mocker.patch(
        "database.lifecycle.TORTOISE_ORM",
        {
            "connections": {
                "default": (
                    "postgres://mcp_log_server:mcp-log-server-local-password"
                    "@127.0.0.1:5432/mcp_log_server"
                ),
            },
            "apps": {},
        },
    )

    async with database_lifespan(object()):
        calls.append("inside")

    assert calls == [
        "init:postgres://mcp_log_server:mcp-log-server-local-password@127.0.0.1:5432/mcp_log_server",
        "inside",
        "close",
    ]
