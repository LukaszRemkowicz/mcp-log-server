"""Shared helpers for host-side command entrypoints."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import environ  # type: ignore[import-untyped]

DEFAULT_STATE_ROOT = Path("/var/lib/mcp-log-server")
LOCAL_COMPOSE_FILE = "docker-compose.yml"
PROD_COMPOSE_FILE = "docker-compose.prod.yml"
DEFAULT_APP_SERVICE = "app"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTAINER_RUNTIME_MARKERS = (
    Path("/.dockerenv"),
    Path("/run/.containerenv"),
)
cli_env = environ.Env()

env_file = REPOSITORY_ROOT / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)


def normalize_environment(environment: str) -> str:
    """Return the canonical environment name."""

    if environment == "production":
        return "prod"
    if environment in {"dev", "development"}:
        return "local"
    if environment in {"local", "prod"}:
        return environment
    raise ValueError(f"Unsupported environment: {environment}")


def get_current_environment() -> str:
    """Return the canonical runtime environment from app settings."""

    from conf import settings

    return normalize_environment(str(settings.ENVIRONMENT))


def get_project_dir(project_dir: Path | None = None) -> Path:
    """Return the project root used for local state fallback paths."""

    if project_dir is not None:
        return project_dir.resolve()
    return Path.cwd().resolve()


def get_state_dir(
    environment: str,
    *,
    project_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return the operational state directory for one environment."""

    configured_state_dir = _cli_env_str("STATE_DIR", default="", env=env).strip()
    if configured_state_dir:
        return Path(configured_state_dir)

    normalized_environment = normalize_environment(environment)
    preferred = DEFAULT_STATE_ROOT / normalized_environment
    if _can_use_preferred_state_dir(preferred):
        return preferred

    return get_project_dir(project_dir) / ".agent" / "state" / normalized_environment


def current_tag_path(
    environment: str = "prod",
    *,
    project_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return the current deployed tag file path."""

    return get_state_dir(environment, project_dir=project_dir, env=env) / "current_tag"


def resolve_prod_tag(
    *,
    required: bool,
    env: Mapping[str, str] | None = None,
) -> str:
    """Return the recorded prod current_tag."""

    tag_file = current_tag_path("prod", env=env)
    try:
        tag = tag_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        if required:
            raise RuntimeError(f"Deployed tag file was not found: {tag_file}")
        return ""

    if tag:
        return tag
    if required:
        raise RuntimeError(f"Deployed tag file is empty: {tag_file}")
    return ""


def is_running_in_container() -> bool:
    """Return whether this process is already running in a container."""

    return any(path.exists() for path in CONTAINER_RUNTIME_MARKERS)


def should_bridge_to_compose() -> bool:
    """Return whether host-side commands should run through Docker Compose."""

    if _cli_env_str("COMMANDS_DISABLE_COMPOSE_BRIDGE", default="") == "1":
        return False
    return not is_running_in_container()


def get_compose_file(environment: str) -> str:
    """Return the compose file for one runtime environment."""

    if normalize_environment(environment) == "prod":
        return PROD_COMPOSE_FILE
    return LOCAL_COMPOSE_FILE


def build_compose_command(environment: str, command: Sequence[str]) -> list[str]:
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
        "--rm",
        get_commands_app_service(),
        *command,
    ]


def get_commands_compose_project_name() -> str:
    """Return an explicit Compose project name for CLI container discovery."""

    project_name = _cli_env_str("COMMANDS_COMPOSE_PROJECT_NAME", default="").strip()
    if project_name:
        return project_name
    return _cli_env_str("COMPOSE_PROJECT_NAME", default="").strip()


def get_commands_app_service() -> str:
    """Return the app service name used by CLI container discovery."""

    return _cli_env_str("COMMANDS_APP_SERVICE", default=DEFAULT_APP_SERVICE).strip()


def should_start_shell_repl() -> bool:
    """Return whether the developer shell should start an interactive REPL."""

    return _cli_env_str("MCP_SHELL_EXIT_AFTER_BOOT", default="") != "1"


def run_compose_command(command: Sequence[str]) -> int:
    """Run one command in the environment-specific Compose app service."""

    return int(
        subprocess.run(
            build_compose_command(get_current_environment(), command),
            check=False,
        ).returncode
    )


def _can_use_preferred_state_dir(preferred: Path) -> bool:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return True
    parent = preferred.parent
    return parent.is_dir() and os.access(parent, os.W_OK)


def _cli_env_str(
    name: str,
    *,
    default: str,
    env: Mapping[str, str] | None = None,
) -> str:
    if env is not None:
        return env.get(name, default)
    return cli_env.str(name, default=default)
