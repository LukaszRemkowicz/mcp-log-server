"""Shared helpers for host-side command entrypoints."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from utils.state_dir import (
    DEFAULT_STATE_ROOT,
    current_tag_path,
    get_project_dir,
    get_state_dir,
    normalize_environment,
    resolve_prod_tag,
)

LOCAL_COMPOSE_FILE = "docker-compose.yml"
PROD_COMPOSE_FILE = "docker-compose.prod.yml"
DEFAULT_APP_SERVICE = "app"
DB_ONLY_COMMAND_NAMES = frozenset(
    {
        "slow-analysis-calls",
        "upload-project-manifest",
        "update-project-manifest",
    }
)
CONTAINER_RUNTIME_MARKERS = (
    Path("/.dockerenv"),
    Path("/run/.containerenv"),
)

__all__ = [
    "DEFAULT_APP_SERVICE",
    "DB_ONLY_COMMAND_NAMES",
    "DEFAULT_STATE_ROOT",
    "LOCAL_COMPOSE_FILE",
    "PROD_COMPOSE_FILE",
    "ComposeRunPolicy",
    "build_compose_command",
    "build_compose_up_command",
    "current_tag_path",
    "ensure_compose_services_started",
    "get_commands_app_service",
    "get_commands_compose_project_name",
    "get_compose_file",
    "get_current_environment",
    "get_project_dir",
    "get_state_dir",
    "is_running_in_container",
    "normalize_environment",
    "resolve_prod_tag",
    "resolve_compose_run_policy",
    "run_compose_command",
    "should_bridge_to_compose",
    "should_start_shell_repl",
]


@dataclass(frozen=True, slots=True)
class ComposeRunPolicy:
    """Describe how one host-side CLI command should run through Compose."""

    preflight_services: tuple[str, ...] = ()
    no_deps: bool = False


def get_current_environment() -> str:
    """Return the canonical runtime environment from app settings."""

    from conf import settings

    return normalize_environment(str(settings.ENVIRONMENT))


def is_running_in_container() -> bool:
    """Return whether this process is already running in a container."""

    return any(path.exists() for path in CONTAINER_RUNTIME_MARKERS)


def should_bridge_to_compose() -> bool:
    """Return whether host-side commands should run through Docker Compose."""

    if os.environ.get("COMMANDS_DISABLE_COMPOSE_BRIDGE", "") == "1":
        return False
    return not is_running_in_container()


def get_compose_file(environment: str) -> str:
    """Return the compose file for one runtime environment."""

    if normalize_environment(environment) == "prod":
        return PROD_COMPOSE_FILE
    return LOCAL_COMPOSE_FILE


def build_compose_command(
    environment: str,
    command: Sequence[str],
    *,
    no_deps: bool = False,
) -> list[str]:
    """Return a Docker Compose run command for one app command."""

    normalized_environment = normalize_environment(environment)
    prefix: list[str] = []
    if normalized_environment == "prod":
        prefix = ["env", f"TAG={resolve_prod_tag(required=True)}"]

    return [
        *prefix,
        "docker",
        "compose",
        "-f",
        get_compose_file(normalized_environment),
        "run",
        *(["--no-deps"] if no_deps else []),
        "--rm",
        get_commands_app_service(),
        *command,
    ]


def build_compose_up_command(environment: str, services: Sequence[str]) -> list[str]:
    """Return a Docker Compose up command for required preflight services."""

    normalized_environment = normalize_environment(environment)
    prefix: list[str] = []
    if normalized_environment == "prod":
        prefix = ["env", f"TAG={resolve_prod_tag(required=True)}"]
    return [
        *prefix,
        "docker",
        "compose",
        "-f",
        get_compose_file(normalized_environment),
        "up",
        "-d",
        *services,
    ]


def ensure_compose_services_started(environment: str, services: Sequence[str]) -> int:
    """Start required Compose services before a host-side command uses local dependencies."""

    return int(
        subprocess.run(
            build_compose_up_command(environment, services),
            check=False,
        ).returncode
    )


def get_commands_compose_project_name() -> str:
    """Return an explicit Compose project name for CLI container discovery."""

    project_name = os.environ.get("COMMANDS_COMPOSE_PROJECT_NAME", "").strip()
    if project_name:
        return project_name
    return os.environ.get("COMPOSE_PROJECT_NAME", "").strip()


def get_commands_app_service() -> str:
    """Return the app service name used by CLI container discovery."""

    return os.environ.get("COMMANDS_APP_SERVICE", DEFAULT_APP_SERVICE).strip()


def should_start_shell_repl() -> bool:
    """Return whether the developer shell should start an interactive REPL."""

    return os.environ.get("MCP_SHELL_EXIT_AFTER_BOOT", "") != "1"


def resolve_compose_run_policy(command: Sequence[str]) -> ComposeRunPolicy:
    """Return the Compose execution policy for one bridged CLI command."""

    cli_args = list(command)
    if cli_args[:3] == ["python", "-m", "cli.main"]:
        cli_args = cli_args[3:]
    if cli_args and cli_args[0] in DB_ONLY_COMMAND_NAMES:
        return ComposeRunPolicy(preflight_services=("db",), no_deps=True)
    return ComposeRunPolicy()


def run_compose_command(command: Sequence[str], *, policy: ComposeRunPolicy | None = None) -> int:
    """Run one command in the environment-specific Compose app service."""

    environment = get_current_environment()
    resolved_policy = policy or resolve_compose_run_policy(command)
    if resolved_policy.preflight_services:
        up_result = ensure_compose_services_started(environment, resolved_policy.preflight_services)
        if up_result != 0:
            return up_result
    return int(
        subprocess.run(
            build_compose_command(environment, command, no_deps=resolved_policy.no_deps),
            check=False,
        ).returncode
    )
