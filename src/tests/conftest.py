from __future__ import annotations

import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from authlib.jose import JsonWebToken
from starlette.testclient import TestClient

from app import create_application
from auth.auth_provider import build_auth_provider
from settings import Settings


@dataclass(slots=True)
class JsonRpcClient:
    """Small test helper for authenticated JSON-RPC POST calls."""

    api_client: TestClient
    mcp_path: str

    def post(
        self,
        *,
        token: str | None,
        data: dict[str, Any],
    ) -> httpx.Response:
        headers = {
            "Accept": "application/json",
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"

        return self.api_client.post(self.mcp_path, headers=headers, json=data)


@pytest.fixture
def settings_fixture() -> Settings:
    return Settings(
        environment="dev",
        host="127.0.0.1",
        port=8001,
        log_level="INFO",
        log_format="text",
        jwt_algorithm="HS256",
        jwt_shared_secret="change-me-local-dev-secret",
        jwt_issuer="mcp-log-server-dev",
        jwt_audience="mcp-log-server",
        jwt_expiration_seconds=86400,
        manifest_path=Path(__file__).resolve().parents[2] / "src/manifests/landingpage.json",
        mcp_path="/mcp",
        mcp_stateless_http=True,
        mcp_json_response=True,
    )


@pytest.fixture
def create_test_jwt_token(settings_fixture: Settings) -> Callable[[str, list[str], str], str]:
    def build_token(subject: str, scopes: list[str], client_id: str) -> str:
        now = int(time.time())
        jwt = JsonWebToken([settings_fixture.jwt_algorithm])
        header = {"alg": settings_fixture.jwt_algorithm, "typ": "JWT"}
        payload = {
            "iss": settings_fixture.jwt_issuer,
            "aud": settings_fixture.jwt_audience,
            "iat": now,
            "exp": now + settings_fixture.jwt_expiration_seconds,
            "sub": subject,
            "client_id": client_id,
            "project_key": "landingpage",
            "scope": " ".join(scopes),
        }
        token = jwt.encode(header, payload, settings_fixture.jwt_shared_secret)
        return token.decode() if isinstance(token, bytes) else str(token)

    return build_token


@pytest.fixture
def api_client(settings_fixture: Settings) -> Generator[TestClient]:
    app = create_application(auth_provider=build_auth_provider(settings_fixture))
    asgi_app = app.http_app(
        path=settings_fixture.mcp_path,
        json_response=settings_fixture.mcp_json_response,
        stateless_http=settings_fixture.mcp_stateless_http,
    )
    with TestClient(asgi_app) as client:
        yield client


@pytest.fixture
def jsonrpc(
    api_client: TestClient,
    settings_fixture: Settings,
) -> JsonRpcClient:
    return JsonRpcClient(
        api_client=api_client,
        mcp_path=settings_fixture.mcp_path,
    )
