"""Developer shell with project helpers preloaded."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from typing import Any

from cli.utils import (
    get_commands_app_service,
    get_commands_compose_project_name,
    should_start_shell_repl,
)
from conf import settings

MCP_PROJECT_SLUG = "mcp-log-server"


def _running_inside_container() -> bool:
    """Return whether this shell is already running inside a container."""

    return os.path.exists("/.dockerenv")


def _find_running_mcp_app_container() -> str | None:
    """Return the running production MCP app container name when available."""

    project_name = get_commands_compose_project_name()
    service_name = get_commands_app_service()
    if project_name:
        container_name = _find_running_compose_service_container(
            project_name=project_name,
            service_name=service_name,
        )
        if container_name is not None:
            return container_name

    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.service={service_name}",
            "--filter",
            "status=running",
            "--format",
            '{{.Names}}\t{{.Image}}\t{{.Label "com.docker.compose.project"}}',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        container_name, image_name, compose_project = _parse_container_candidate(line)
        if any(
            MCP_PROJECT_SLUG in value for value in (container_name, image_name, compose_project)
        ):
            return container_name
    return None


def _find_running_compose_service_container(
    *,
    project_name: str,
    service_name: str,
) -> str | None:
    """Return the running Compose service container name for an explicit project."""

    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
            "--filter",
            f"label=com.docker.compose.service={service_name}",
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    container_names = [name.strip() for name in result.stdout.splitlines() if name.strip()]
    return container_names[0] if container_names else None


def _parse_container_candidate(line: str) -> tuple[str, str, str]:
    """Return container discovery fields from one docker-ps formatted line."""

    parts = [part.strip() for part in line.split("\t", maxsplit=2)]
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def reexec_inside_running_mcp_app_container_if_needed() -> None:
    """Run host-side `uv run shell` inside the production app container when present."""

    if _running_inside_container():
        return
    container_name = _find_running_mcp_app_container()
    if container_name is None:
        return
    docker_args = ["docker", "exec", "-it"]
    docker_args.extend([container_name, "uv", "run", "shell"])
    os.execvp("docker", docker_args)


def _loaded_from_shell_entrypoint() -> bool:
    """Return whether this module is being loaded by the shell console script."""

    return os.path.basename(sys.argv[0]) in {"shell", "shell.py"}


if _loaded_from_shell_entrypoint():
    reexec_inside_running_mcp_app_container_if_needed()

from core.types import LogWorkspace
from database.config import TORTOISE_ORM
from database.lifecycle import close_database, initialize_database
from database.models import (
    AgentCall,
    CollectLogs,
    CollectLogsSource,
    McpCaller,
    ProjectManifest,
    Task,
)
from database.schemas import (
    AgentCallCreate,
    AgentCallFilter,
    AgentCallUpdate,
    AsyncTaskResult,
    TaskCreate,
    TaskListOut,
    TaskOut,
    TaskUpdate,
)
from database.services.agent_calls import AgentCallService
from database.services.project_manifests import ProjectManifestService
from database.services.tasks import TaskService
from database.types import (
    AgentCallEvent,
    CollectLogsSourceStatus,
    LogSourceType,
    LogStream,
    TaskStatus,
    TaskType,
)
from services.agent_calls import AgentCallAuditService, AgentCallCreateError
from services.log_collection import LogCollectionService
from tasks import collect_logs_task, task

SHELL_IMPORT_LINES = [
    "from conf import settings",
    "from database.config import TORTOISE_ORM",
    (
        "from database.models import McpCaller, AgentCall, CollectLogs, "
        "CollectLogsSource, ProjectManifest, Task"
    ),
    (
        "from database.schemas import AgentCallCreate, AgentCallFilter, AgentCallUpdate, "
        "AsyncTaskResult, TaskCreate, TaskUpdate, TaskOut, TaskListOut"
    ),
    "from core.types import LogWorkspace",
    (
        "from database.types import AgentCallEvent, CollectLogsSourceStatus, "
        "LogSourceType, LogStream, TaskStatus, TaskType"
    ),
    "from database.services.agent_calls import AgentCallService",
    "from database.services.project_manifests import ProjectManifestService",
    "from database.services.tasks import TaskService",
    "from services.agent_calls import AgentCallAuditService, AgentCallCreateError",
    "from services.log_collection import LogCollectionService",
    "from tasks import task, collect_logs_task",
]


def build_shell_namespace() -> dict[str, Any]:
    """Return names preloaded into the developer shell."""

    return {
        "settings": settings,
        "TORTOISE_ORM": TORTOISE_ORM,
        "McpCaller": McpCaller,
        "AgentCall": AgentCall,
        "AgentCallEvent": AgentCallEvent,
        "AgentCallService": AgentCallService,
        "AgentCallAuditService": AgentCallAuditService,
        "AgentCallCreate": AgentCallCreate,
        "AgentCallCreateError": AgentCallCreateError,
        "AgentCallFilter": AgentCallFilter,
        "AgentCallUpdate": AgentCallUpdate,
        "CollectLogs": CollectLogs,
        "CollectLogsSource": CollectLogsSource,
        "CollectLogsSourceStatus": CollectLogsSourceStatus,
        "LogCollectionService": LogCollectionService,
        "LogSourceType": LogSourceType,
        "LogStream": LogStream,
        "LogWorkspace": LogWorkspace,
        "ProjectManifest": ProjectManifest,
        "ProjectManifestService": ProjectManifestService,
        "Task": Task,
        "TaskCreate": TaskCreate,
        "TaskListOut": TaskListOut,
        "TaskOut": TaskOut,
        "TaskService": TaskService,
        "TaskStatus": TaskStatus,
        "TaskType": TaskType,
        "TaskUpdate": TaskUpdate,
        "AsyncTaskResult": AsyncTaskResult,
        "collect_logs_task": collect_logs_task,
        "task": task,
    }


def _start_ipython(user_ns: dict[str, Any]) -> None:
    """Start IPython with the given user namespace."""

    from IPython import start_ipython

    start_ipython(
        argv=[],
        user_ns=user_ns,
        display_banner=False,
    )


def print_shell_imports() -> None:
    """Print copy-paste import lines for names loaded into the shell."""

    print("Preloaded imports:")
    for import_line in SHELL_IMPORT_LINES:
        print(import_line)


def close_shell_database() -> None:
    """Close shell database connections, ignoring IPython loop ownership noise."""

    try:
        asyncio.run(close_database())
    except RuntimeError as exc:
        if "attached to a different loop" not in str(exc):
            raise


async def _initialize_shell() -> dict[str, Any]:
    """Initialize the database and return the shell namespace."""

    await initialize_database(TORTOISE_ORM)
    return build_shell_namespace()


def run_shell(*, start_repl: bool = True) -> int:
    """Initialize the database, then start the interactive developer shell."""

    user_ns = asyncio.run(_initialize_shell())
    try:
        if not start_repl:
            print_shell_imports()
            return 0

        print("Database initialized. Use top-level await for ORM calls.")
        print_shell_imports()
        _start_ipython(user_ns)
        return 0
    finally:
        close_shell_database()


def main() -> None:
    """Run the developer shell command."""

    raise SystemExit(run_shell(start_repl=should_start_shell_repl()))
