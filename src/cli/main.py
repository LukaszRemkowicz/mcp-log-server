"""Root Typer application for project commands."""

from __future__ import annotations

import shlex
import sys

import typer

from cli.commands.generate_dev_jwt import generate_dev_jwt
from cli.commands.upload_project_manifest import update_project_manifest, upload_project_manifest
from cli.utils import (
    build_compose_command,
    get_current_environment,
    run_compose_command,
    should_bridge_to_compose,
)

DRY_RUN_FLAGS = frozenset({"--dry-run", "-n"})

app = typer.Typer(
    help=(
        "Project maintenance commands for mcp-log-server. Use --help here to "
        "discover local Typer commands for JWT generation, manifest uploads, "
        "and other developer operations."
    )
)
app.command(
    "generate-dev-jwt",
    help="Generate signed local development JWTs for MCP clients.",
)(generate_dev_jwt)
app.command("upload-project-manifest")(upload_project_manifest)
app.command("update-project-manifest")(update_project_manifest)


def _extract_dry_run(args: list[str]) -> tuple[list[str], bool]:
    """Remove bridge dry-run flags from CLI args."""

    command_args = [arg for arg in args if arg not in DRY_RUN_FLAGS]
    return command_args, any(arg in DRY_RUN_FLAGS for arg in args)


def _run(command_args: list[str]) -> None:
    """Run the project command app with explicit command args."""

    command_args, dry_run = _extract_dry_run(command_args)
    if should_bridge_to_compose():
        compose_command = ["python", "-m", "cli.main", *command_args]
        if dry_run:
            print(shlex.join(build_compose_command(get_current_environment(), compose_command)))
            raise SystemExit(0)
        raise SystemExit(run_compose_command(compose_command))
    app()


def main() -> None:
    """Run the project command app."""

    _run(sys.argv[1:])


if __name__ == "__main__":
    main()
