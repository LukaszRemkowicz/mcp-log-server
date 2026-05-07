"""Generate example JWTs for local development."""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from joserfc import jwt
from joserfc.jwk import OctKey

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = REPOSITORY_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from auth.scopes import (  # noqa: E402
    CONTAINER_FILES_READ_SCOPE,
    LOGS_COLLECT_SCOPE,
    MCP_HEALTH_READ_SCOPE,
    MCP_STATUS_READ_SCOPE,
    PROJECTS_READ_SCOPE,
    WORKFLOW_BOOTSTRAP_SCOPE,
    WORKFLOW_SKILLS_READ_SCOPE,
)
from settings import Settings, get_settings  # noqa: E402


def build_example_token_payloads(settings: Settings) -> dict[str, dict[str, object]]:
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
            "client_id": "workflow-agent",
            "client_type": "workflow_agent",
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
            "client_id": "codex-agent",
            "client_type": "codex",
            "scope": " ".join(
                (
                    CONTAINER_FILES_READ_SCOPE,
                    LOGS_COLLECT_SCOPE,
                    PROJECTS_READ_SCOPE,
                    MCP_STATUS_READ_SCOPE,
                    MCP_HEALTH_READ_SCOPE,
                )
            ),
        },
    }


def build_example_tokens(settings: Settings) -> dict[str, str]:
    """Return signed example JWTs for local development."""

    signing_key = OctKey.import_key(settings.JWT_SHARED_SECRET)
    header = {"alg": settings.JWT_ALGORITHM, "typ": "JWT"}
    tokens: dict[str, str] = {}
    for token_name, payload in build_example_token_payloads(settings).items():
        tokens[token_name] = jwt.encode(
            header,
            payload,
            signing_key,
            algorithms=[settings.JWT_ALGORITHM],
        )
    return tokens


def main() -> None:
    """Print example JWTs for local development."""

    settings = get_settings()
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = {
        **build_example_tokens(settings),
        "created_at": generated_at,
        "updated_at": generated_at,
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
