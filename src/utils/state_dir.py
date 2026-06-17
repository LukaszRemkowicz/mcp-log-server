"""Project state-directory resolution helpers.

This module intentionally uses only the Python standard library. Release shell
scripts can call it without depending on the application dependency environment,
while Python CLI helpers can import the same policy directly.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from pathlib import Path

DEFAULT_STATE_ROOT = Path("/var/lib/mcp-log-server")


def normalize_environment(environment: str) -> str:
    """Return the canonical environment name."""

    if environment == "production":
        return "prod"
    if environment in {"dev", "development"}:
        return "local"
    if environment in {"local", "prod"}:
        return environment
    raise ValueError(f"Unsupported environment: {environment}")


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

    configured_state_dir = _env_str("STATE_DIR", default="", env=env).strip()
    if configured_state_dir:
        return Path(configured_state_dir)

    normalized_environment = normalize_environment(environment)
    preferred = DEFAULT_STATE_ROOT / normalized_environment
    if normalized_environment == "prod":
        return preferred
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
    """Return the explicit or recorded prod tag."""

    explicit_tag = _env_str("TAG", default="", env=env).strip()
    if explicit_tag:
        return explicit_tag

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


def _can_use_preferred_state_dir(preferred: Path) -> bool:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return True
    parent = preferred.parent
    return parent.is_dir() and os.access(parent, os.W_OK)


def _env_str(
    name: str,
    *,
    default: str,
    env: Mapping[str, str] | None = None,
) -> str:
    if env is not None:
        return env.get(name, default)
    return os.environ.get(name, default)


def main() -> None:
    """Print the resolved state directory for shell scripts."""

    parser = argparse.ArgumentParser(description="Resolve mcp-log-server state directory.")
    parser.add_argument("environment")
    parser.add_argument("--project-dir", type=Path, default=None)
    args = parser.parse_args()
    print(get_state_dir(args.environment, project_dir=args.project_dir))


if __name__ == "__main__":
    main()
