"""Root Typer application for project commands."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Annotated

import typer

from cli.commands.generate_dev_jwt import generate_dev_jwt as run_generate_dev_jwt
from cli.commands.slow_analysis_calls import run_slow_analysis_calls
from cli.commands.upload_project_manifest import (
    update_project_manifest as run_update_project_manifest,
)
from cli.commands.upload_project_manifest import (
    upload_project_manifest as run_upload_project_manifest,
)
from cli.utils import (
    build_compose_command,
    build_compose_up_command,
    ensure_compose_services_started,
    get_current_environment,
    resolve_compose_run_policy,
    run_compose_command,
    should_bridge_to_compose,
)

DRY_RUN_FLAGS = frozenset({"--dry-run", "-n"})
HELP_FLAGS = frozenset({"--help", "-h"})
HOST_DB_BOOTSTRAP_COMMANDS = frozenset({"generate-dev-jwt"})

app = typer.Typer(
    help=(
        "Project maintenance commands for mcp-log-server. Use --help here to "
        "discover local Typer commands for JWT generation, manifest uploads, "
        "and other developer operations."
    )
)


@app.command("generate-dev-jwt", help="Generate signed local development JWTs for MCP clients.")
def generate_dev_jwt(
    output_file: Annotated[
        Path | None,
        typer.Option(
            "--output-file",
            "-o",
            help="Write the generated token JSON to this file instead of stdout.",
        ),
    ] = None,
    exp_time_hours: Annotated[
        int | None,
        typer.Option(
            "--exp-time",
            min=1,
            help=(
                "Override token lifetime in hours. Defaults to JWT_EXPIRATION_SECONDS "
                "from settings."
            ),
        ),
    ] = None,
) -> None:
    """Generate signed local development JWTs for MCP clients."""

    run_generate_dev_jwt(output_file=output_file, exp_time_hours=exp_time_hours)


@app.command("slow-analysis-calls", help="Review slow snapshot-analysis MCP calls.")
def slow_analysis_calls(
    min_duration: Annotated[
        float,
        typer.Option(
            "--min-duration",
            min=0.0,
            help="Only show analysis calls whose duration is at least this many seconds.",
        ),
    ] = 1.0,
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            min=1,
            max=200,
            help="Maximum number of slow calls to print.",
        ),
    ] = 20,
    tool_name: Annotated[
        str | None,
        typer.Option(
            "--tool-name",
            help="Restrict output to one snapshot-analysis tool name.",
        ),
    ] = None,
    project_name: Annotated[
        str | None,
        typer.Option(
            "--project-name",
            help="Restrict output to one manifest project name.",
        ),
    ] = None,
    success: Annotated[
        bool | None,
        typer.Option(
            "--success/--failed",
            help="Restrict output to successful or failed analysis calls.",
        ),
    ] = None,
) -> None:
    """Review slow snapshot-analysis MCP calls."""

    run_slow_analysis_calls(
        min_duration=min_duration,
        limit=limit,
        tool_name=tool_name,
        project_name=project_name,
        success=success,
    )


@app.command("upload-project-manifest")
def upload_project_manifest(
    project_name: Annotated[
        str | None,
        typer.Argument(help="Project key to load from <path>/<project>.json."),
    ] = None,
    all_projects: Annotated[
        bool,
        typer.Option("--all", help="Upload every manifest JSON file from --path."),
    ] = False,
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            help="Directory with project manifest JSON files. Defaults to PROJECT_MANIFESTS_PATH.",
        ),
    ] = None,
) -> None:
    """Upload one or all configured project manifests into the database."""

    run_upload_project_manifest(project_name=project_name, all_projects=all_projects, path=path)


@app.command("update-project-manifest")
def update_project_manifest(
    project_name: Annotated[
        str | None,
        typer.Option(
            "--project",
            help="Project key to update from <path>/<project>.json. Required unless --all is used.",
        ),
    ] = None,
    all_projects: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Update every manifest JSON file from --path instead of using --project.",
        ),
    ] = False,
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            help="Directory with project manifest JSON files. Defaults to PROJECT_MANIFESTS_PATH.",
        ),
    ] = None,
) -> None:
    """Update one or all configured project manifests in the database."""

    run_update_project_manifest(project_name=project_name, all_projects=all_projects, path=path)


def _extract_dry_run(args: list[str]) -> tuple[list[str], bool]:
    """Remove bridge dry-run flags from CLI args."""

    command_args = [arg for arg in args if arg not in DRY_RUN_FLAGS]
    return command_args, any(arg in DRY_RUN_FLAGS for arg in args)


def _run(command_args: list[str]) -> None:
    """Run the project command app with explicit command args."""

    command_args, dry_run = _extract_dry_run(command_args)
    if command_args and command_args[0] in HOST_DB_BOOTSTRAP_COMMANDS:
        if should_bridge_to_compose() and not HELP_FLAGS.intersection(command_args):
            environment = get_current_environment()
            if dry_run:
                print(shlex.join(build_compose_up_command(environment, ("db",))))
                raise SystemExit(0)
            exit_code = ensure_compose_services_started(environment, ("db",))
            if exit_code != 0:
                raise SystemExit(exit_code)
        app()
        return
    if should_bridge_to_compose() and not HELP_FLAGS.intersection(command_args):
        compose_command = ["python", "-m", "cli.main", *command_args]
        policy = resolve_compose_run_policy(compose_command)
        if dry_run:
            if policy.preflight_services:
                print(
                    shlex.join(
                        build_compose_up_command(
                            get_current_environment(),
                            policy.preflight_services,
                        )
                    )
                )
            print(
                shlex.join(
                    build_compose_command(
                        get_current_environment(),
                        compose_command,
                        no_deps=policy.no_deps,
                    )
                )
            )
            raise SystemExit(0)
        raise SystemExit(run_compose_command(compose_command, policy=policy))
    app()


def main() -> None:
    """Run the project command app."""

    _run(sys.argv[1:])


if __name__ == "__main__":
    main()
