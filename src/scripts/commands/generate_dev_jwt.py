"""Typer command for local development JWT generation."""

from __future__ import annotations

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
from settings import Settings, get_settings

DEFAULT_WORKFLOW_CLIENT_ID = "workflow-agent"
DEFAULT_WORKFLOW_CLIENT_TYPE = "workflow_agent"
DEFAULT_CODEX_CLIENT_ID = "codex-agent"
DEFAULT_CODEX_CLIENT_TYPE = "codex"


def build_example_token_payloads(
    settings: Settings,
    *,
    workflow_client_id: str = DEFAULT_WORKFLOW_CLIENT_ID,
    workflow_client_type: str = DEFAULT_WORKFLOW_CLIENT_TYPE,
    codex_client_id: str = DEFAULT_CODEX_CLIENT_ID,
    codex_client_type: str = DEFAULT_CODEX_CLIENT_TYPE,
) -> dict[str, dict[str, object]]:
    """Return example JWT payloads for local development clients."""

    now = int(time.time())
    exp = now + settings.JWT_EXPIRATION_SECONDS
    common_claims = {
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "iat": now,
        "exp": exp,
        "allowed_projects": ["landingpage"],
    }
    return {
        "workflow_agent": {
            **common_claims,
            "sub": "workflow-agent",
            "client_id": workflow_client_id,
            "client_type": workflow_client_type,
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
            "sub": "codex-agent",
            "client_id": codex_client_id,
            "client_type": codex_client_type,
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
    workflow_client_id: str = DEFAULT_WORKFLOW_CLIENT_ID,
    workflow_client_type: str = DEFAULT_WORKFLOW_CLIENT_TYPE,
    codex_client_id: str = DEFAULT_CODEX_CLIENT_ID,
    codex_client_type: str = DEFAULT_CODEX_CLIENT_TYPE,
) -> dict[str, str]:
    """Return signed example JWTs for local development."""

    signing_key = OctKey.import_key(settings.JWT_SHARED_SECRET)
    header = {"alg": settings.JWT_ALGORITHM, "typ": "JWT"}
    tokens: dict[str, str] = {}
    payloads = build_example_token_payloads(
        settings,
        workflow_client_id=workflow_client_id,
        workflow_client_type=workflow_client_type,
        codex_client_id=codex_client_id,
        codex_client_type=codex_client_type,
    )
    for token_name, payload in payloads.items():
        tokens[token_name] = jwt.encode(
            header,
            payload,
            signing_key,
            algorithms=[settings.JWT_ALGORITHM],
        )
    return tokens


def build_example_token_response(
    settings: Settings,
    *,
    workflow_client_id: str = DEFAULT_WORKFLOW_CLIENT_ID,
    workflow_client_type: str = DEFAULT_WORKFLOW_CLIENT_TYPE,
    codex_client_id: str = DEFAULT_CODEX_CLIENT_ID,
    codex_client_type: str = DEFAULT_CODEX_CLIENT_TYPE,
) -> dict[str, str]:
    """Return signed example tokens with generation timestamps."""

    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        **build_example_tokens(
            settings,
            workflow_client_id=workflow_client_id,
            workflow_client_type=workflow_client_type,
            codex_client_id=codex_client_id,
            codex_client_type=codex_client_type,
        ),
        "created_at": generated_at,
        "updated_at": generated_at,
    }


def generate_dev_jwt(
    output_file: Path | None = typer.Option(
        None,
        "--output-file",
        "-o",
        help="Write the generated token JSON to this file instead of stdout.",
    ),
    workflow_client_id: str = typer.Option(
        DEFAULT_WORKFLOW_CLIENT_ID,
        help="client_id claim for the workflow_agent token.",
    ),
    workflow_client_type: str = typer.Option(
        DEFAULT_WORKFLOW_CLIENT_TYPE,
        help="client_type claim for the workflow_agent token.",
    ),
    codex_client_id: str = typer.Option(
        DEFAULT_CODEX_CLIENT_ID,
        help="client_id claim for the codex_agent token.",
    ),
    codex_client_type: str = typer.Option(
        DEFAULT_CODEX_CLIENT_TYPE,
        help="client_type claim for the codex_agent token.",
    ),
) -> None:
    """Generate signed local development JWTs for MCP clients.

    Print a JSON payload containing workflow_agent and codex_agent bearer tokens
    plus created_at and updated_at timestamps, or write the same JSON to
    --output-file when provided. Use these tokens for local MCP HTTP calls, curl
    examples, and E2E checks against the development shared secret. Override
    client_id or client_type when testing a different local caller identity.
    """

    payload = build_example_token_response(
        get_settings(),
        workflow_client_id=workflow_client_id,
        workflow_client_type=workflow_client_type,
        codex_client_id=codex_client_id,
        codex_client_type=codex_client_type,
    )
    token_json = json.dumps(payload, indent=2)
    if output_file is None:
        print(token_json)
        return

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(f"{token_json}\n")


__all__ = ["generate_dev_jwt"]
