"""Typer command for local development JWT generation."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import typer
from joserfc import jwt
from joserfc.jwk import OctKey

from auth.scopes import (
    CONTAINER_FILES_READ_SCOPE,
    LOGS_COLLECT_SCOPE,
    MCP_HEALTH_READ_SCOPE,
    MCP_STATUS_READ_SCOPE,
    PROJECTS_READ_SCOPE,
    SESSION_CLOSE_SCOPE,
    WORKFLOW_BOOTSTRAP_SCOPE,
    WORKFLOW_SKILLS_READ_SCOPE,
)
from conf import Settings, get_settings
from core.types import LogWorkspace
from database.config import TORTOISE_ORM
from database.lifecycle import close_database, initialize_database
from database.models import McpCaller

TOKEN_WORKSPACES = {
    "workflow_agent": LogWorkspace.WORKFLOW,
    "codex_agent": LogWorkspace.SESSION,
}


async def _get_token_caller(token_name: str, workspace: LogWorkspace) -> McpCaller:
    """Return the single DB caller row used for one generated token."""

    callers = await McpCaller.objects.filter(workspace=workspace).order_by("id")
    if not callers:
        raise RuntimeError(
            f"Missing McpCaller row for workspace '{workspace.value}' while generating "
            f"{token_name} token."
        )
    if len(callers) > 1:
        caller_names = ", ".join(f"{caller.client_id}/{caller.client_type}" for caller in callers)
        raise RuntimeError(
            f"Found multiple McpCaller rows for workspace '{workspace.value}' while generating "
            f"{token_name} token: {caller_names}. Keep one caller row per generated token."
        )
    return callers[0]


async def _get_token_callers() -> dict[str, McpCaller]:
    """Return DB caller rows for generated token names."""

    return {
        token_name: await _get_token_caller(token_name, workspace)
        for token_name, workspace in TOKEN_WORKSPACES.items()
    }


def build_example_token_payloads(
    settings: Settings,
    *,
    callers: dict[str, McpCaller],
) -> dict[str, dict[str, object]]:
    """Return example JWT payloads for local development clients."""

    now = int(time.time())
    exp = now + settings.JWT_EXPIRATION_SECONDS
    common_claims = {
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "iat": now,
        "exp": exp,
    }
    workflow_caller = callers["workflow_agent"]
    codex_caller = callers["codex_agent"]
    return {
        "workflow_agent": {
            **common_claims,
            "sub": workflow_caller.client_id,
            "client_id": workflow_caller.client_id,
            "client_type": workflow_caller.client_type,
            "allowed_projects": workflow_caller.allowed_projects,
            "scope": " ".join(
                (
                    LOGS_COLLECT_SCOPE,
                    PROJECTS_READ_SCOPE,
                    WORKFLOW_BOOTSTRAP_SCOPE,
                    WORKFLOW_SKILLS_READ_SCOPE,
                    MCP_STATUS_READ_SCOPE,
                    MCP_HEALTH_READ_SCOPE,
                )
            ),
        },
        "codex_agent": {
            **common_claims,
            "sub": codex_caller.client_id,
            "client_id": codex_caller.client_id,
            "client_type": codex_caller.client_type,
            "allowed_projects": codex_caller.allowed_projects,
            "scope": " ".join(
                (
                    CONTAINER_FILES_READ_SCOPE,
                    LOGS_COLLECT_SCOPE,
                    PROJECTS_READ_SCOPE,
                    SESSION_CLOSE_SCOPE,
                    MCP_STATUS_READ_SCOPE,
                    MCP_HEALTH_READ_SCOPE,
                )
            ),
        },
    }


def build_example_tokens(
    settings: Settings,
    *,
    callers: dict[str, McpCaller],
) -> dict[str, str]:
    """Return signed example JWTs for local development."""

    signing_key = OctKey.import_key(settings.JWT_SHARED_SECRET)
    header = {"alg": settings.JWT_ALGORITHM, "typ": "JWT"}
    tokens: dict[str, str] = {}
    payloads = build_example_token_payloads(
        settings,
        callers=callers,
    )
    for token_name, payload in payloads.items():
        tokens[token_name] = jwt.encode(
            header,
            payload,
            signing_key,
            algorithms=[settings.JWT_ALGORITHM],
        )
    return tokens


async def build_example_token_response(
    settings: Settings | None = None,
) -> dict[str, str]:
    """Return signed example tokens with generation timestamps."""

    settings = settings or get_settings()
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        **build_example_tokens(
            settings,
            callers=await _get_token_callers(),
        ),
        "created_at": generated_at,
        "updated_at": generated_at,
    }


async def _build_example_token_response_with_database(settings: Settings) -> dict[str, str]:
    """Initialize the database, build tokens from caller rows, and close connections."""

    await initialize_database(TORTOISE_ORM)
    try:
        return await build_example_token_response(settings)
    finally:
        await close_database()


def generate_dev_jwt(
    output_file: Path | None = typer.Option(
        None,
        "--output-file",
        "-o",
        help="Write the generated token JSON to this file instead of stdout.",
    ),
) -> None:
    """Generate signed local development JWTs for MCP clients.

    Print a JSON payload containing workflow_agent and codex_agent bearer tokens
    plus created_at and updated_at timestamps, or write the same JSON to
    --output-file when provided. Use these tokens for local MCP HTTP calls, curl
    examples, and E2E checks against the configured shared secret. The
    client_id, client_type, and allowed_projects claims come from McpCaller
    database rows.
    """

    try:
        payload = asyncio.run(_build_example_token_response_with_database(get_settings()))
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    token_json = json.dumps(payload, indent=2)
    if output_file is None:
        print(token_json)
        return

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(f"{token_json}\n")


__all__ = ["generate_dev_jwt"]
