"""Root Typer application for project commands."""

from __future__ import annotations

import typer

from scripts.commands.generate_dev_jwt import generate_dev_jwt
from scripts.commands.upload_project_manifest import (
    update_project_manifest,
    update_project_manifest_internal,
    upload_project_manifest,
    upload_project_manifest_internal,
)

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
app.command("upload-project-manifest-internal", hidden=True)(upload_project_manifest_internal)
app.command("update-project-manifest")(update_project_manifest)
app.command("update-project-manifest-internal", hidden=True)(update_project_manifest_internal)


def main() -> None:
    """Run the project command app."""

    app()


if __name__ == "__main__":
    main()
