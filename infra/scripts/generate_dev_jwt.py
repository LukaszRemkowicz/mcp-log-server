"""Generate example JWTs for local development."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from joserfc import jwt
from joserfc.jwk import OctKey

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = REPOSITORY_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from auth.scopes import (  # noqa: E402
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
    exp = now + settings.jwt_expiration_seconds
    common_claims = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": exp,
        "project_key": "landingpage",
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

    signing_key = OctKey.import_key(settings.jwt_shared_secret)
    header = {"alg": settings.jwt_algorithm, "typ": "JWT"}
    tokens: dict[str, str] = {}
    for token_name, payload in build_example_token_payloads(settings).items():
        tokens[token_name] = jwt.encode(
            header,
            payload,
            signing_key,
            algorithms=[settings.jwt_algorithm],
        )
    return tokens


def main() -> None:
    """Print example JWTs for local development."""

    settings = get_settings()
    print(json.dumps(build_example_tokens(settings), indent=2))


if __name__ == "__main__":
    main()
