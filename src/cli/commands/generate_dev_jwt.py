"""Local development JWT generation implementation."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
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
from database.models import McpCaller
from decorators import async_, db

TOKEN_WORKSPACES = {
    "workflow_agent": LogWorkspace.WORKFLOW,
    "codex_agent": LogWorkspace.SESSION,
}


@dataclass(frozen=True, slots=True)
class TokenCaller:
    """Caller identity claims used for one generated token."""

    client_id: str
    client_type: str


DEFAULT_TOKEN_CALLERS = {
    "workflow_agent": TokenCaller(
        client_id="workflow-agent",
        client_type="workflow_agent",
    ),
    "codex_agent": TokenCaller(
        client_id="codex-agent",
        client_type="codex",
    ),
}


async def _get_token_caller(token_name: str, workspace: LogWorkspace) -> TokenCaller:
    """Return DB caller claims, or default claims when no row exists."""

    callers = await McpCaller.objects.filter(workspace=workspace).order_by("id")
    if not callers:
        return DEFAULT_TOKEN_CALLERS[token_name]
    expected_caller = DEFAULT_TOKEN_CALLERS[token_name]
    matching_default_callers = [
        caller
        for caller in callers
        if caller.client_id == expected_caller.client_id
        and caller.client_type == expected_caller.client_type
    ]
    if len(matching_default_callers) == 1:
        caller = matching_default_callers[0]
        return TokenCaller(
            client_id=caller.client_id,
            client_type=caller.client_type,
        )
    if len(callers) > 1:
        caller_names = ", ".join(f"{caller.client_id}/{caller.client_type}" for caller in callers)
        raise RuntimeError(
            f"Found multiple McpCaller rows for workspace '{workspace.value}' while generating "
            f"{token_name} token: {caller_names}. Keep one caller row per generated token."
        )
    caller = callers[0]
    return TokenCaller(
        client_id=caller.client_id,
        client_type=caller.client_type,
    )


async def _get_token_callers() -> dict[str, TokenCaller]:
    """Return caller claims for generated token names."""

    return {
        token_name: await _get_token_caller(token_name, workspace)
        for token_name, workspace in TOKEN_WORKSPACES.items()
    }


def build_example_token_payloads(
    settings: Settings,
    *,
    callers: dict[str, TokenCaller],
    exp_time_hours: int | None = None,
) -> dict[str, dict[str, object]]:
    """Return example JWT payloads for local development clients."""

    now = int(time.time())
    expiration_seconds = (
        settings.JWT_EXPIRATION_SECONDS if exp_time_hours is None else exp_time_hours * 60 * 60
    )
    exp = now + expiration_seconds
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
            "scope": " ".join(
                (
                    CONTAINER_FILES_READ_SCOPE,
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
    callers: dict[str, TokenCaller],
    exp_time_hours: int | None = None,
) -> dict[str, str]:
    """Return signed example JWTs for local development."""

    signing_key = OctKey.import_key(settings.JWT_SHARED_SECRET)
    header = {"alg": settings.JWT_ALGORITHM, "typ": "JWT"}
    tokens: dict[str, str] = {}
    payloads = build_example_token_payloads(
        settings,
        callers=callers,
        exp_time_hours=exp_time_hours,
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
    *,
    exp_time_hours: int | None = None,
) -> dict[str, str]:
    """Return signed example tokens with generation timestamps."""

    settings = settings or get_settings()
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        **build_example_tokens(
            settings,
            callers=await _get_token_callers(),
            exp_time_hours=exp_time_hours,
        ),
        "created_at": generated_at,
        "updated_at": generated_at,
    }


@async_
@db
async def generate_dev_jwt(
    output_file: Path | None = None,
    exp_time_hours: int | None = None,
) -> None:
    """Generate signed local development JWTs for MCP clients.

    Print a JSON payload containing workflow_agent and codex_agent bearer tokens
    plus created_at and updated_at timestamps, or write the same JSON to
    --output-file when provided. Use these tokens for local MCP HTTP calls, curl
    examples, and E2E checks against the configured shared secret. The
    client_id and client_type claims come from McpCaller database rows when
    present, or built-in defaults when missing. Project access intentionally
    stays in McpCaller.allowed_projects and is attached by request middleware.
    """

    try:
        payload = await build_example_token_response(get_settings(), exp_time_hours=exp_time_hours)
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    token_json = json.dumps(payload, indent=2)
    if output_file is None:
        print(token_json)
        return

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(f"{token_json}\n")
