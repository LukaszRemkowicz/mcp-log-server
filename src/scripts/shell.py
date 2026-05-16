"""Developer shell with project helpers preloaded."""

from __future__ import annotations

import asyncio
import os
from typing import Any

os.environ.setdefault("DATABASE_HOST", "127.0.0.1")
os.environ.setdefault("DATABASE_PORT", os.environ.get("DATABASE_PORT_HOST", "5437"))

from conf import settings
from core.types import LogWorkspace
from database.config import TORTOISE_ORM
from database.lifecycle import close_database, initialize_database
from database.models import (
    AgentCall,
    Authentication,
    CollectLogs,
    CollectLogsSource,
    ProjectManifest,
)
from database.schemas import AgentCallCreate, AgentCallFilter, AgentCallUpdate
from database.services.agent_calls import AgentCallService
from database.services.project_manifests import ProjectManifestService
from database.types import AgentCallEvent, CollectLogsSourceStatus, LogSourceType, LogStream
from services.agent_calls import AgentCallAuditService, AgentCallCreateError
from services.log_collection import LogCollectionService

SHELL_EXIT_AFTER_BOOT_ENV = "MCP_SHELL_EXIT_AFTER_BOOT"

SHELL_IMPORT_LINES = [
    "from conf import settings",
    "from database.config import TORTOISE_ORM",
    (
        "from database.models import Authentication, AgentCall, CollectLogs, "
        "CollectLogsSource, ProjectManifest"
    ),
    "from database.schemas import AgentCallCreate, AgentCallFilter, AgentCallUpdate",
    "from core.types import LogWorkspace",
    (
        "from database.types import AgentCallEvent, CollectLogsSourceStatus, "
        "LogSourceType, LogStream"
    ),
    "from database.services.agent_calls import AgentCallService",
    "from database.services.project_manifests import ProjectManifestService",
    "from services.agent_calls import AgentCallAuditService, AgentCallCreateError",
    "from services.log_collection import LogCollectionService",
]


def build_shell_namespace() -> dict[str, Any]:
    """Return names preloaded into the developer shell."""

    return {
        "settings": settings,
        "TORTOISE_ORM": TORTOISE_ORM,
        "Authentication": Authentication,
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

    start_repl = os.getenv(SHELL_EXIT_AFTER_BOOT_ENV) != "1"
    raise SystemExit(run_shell(start_repl=start_repl))
