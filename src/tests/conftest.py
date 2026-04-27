from __future__ import annotations

import json
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import OctKey
from starlette.testclient import TestClient

import dependencies
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


@dataclass(slots=True)
class JsonRpcFixture:
    """Provide JSON-RPC helpers for default and custom app settings."""

    client: JsonRpcClient

    def post(self, *, token: str | None, data: dict[str, Any]) -> httpx.Response:
        return self.client.post(token=token, data=data)

    @contextmanager
    def with_settings(self, settings: Settings) -> Generator[JsonRpcClient]:
        """Create a temporary JSON-RPC client for a custom settings object."""

        with patch.object(dependencies, "get_settings", return_value=settings):
            app = create_application(auth_provider=build_auth_provider(settings))
            asgi_app = app.http_app(
                path=settings.MCP_PATH,
                json_response=settings.MCP_JSON_RESPONSE,
                stateless_http=settings.MCP_STATELESS_HTTP,
            )
            with TestClient(asgi_app) as api_client:
                yield JsonRpcClient(
                    api_client=api_client,
                    mcp_path=settings.MCP_PATH,
                )


@dataclass(slots=True)
class FileSourceManifestFactory:
    """Create temporary single-file manifests for collection tests."""

    tmp_path: Path

    def create(
        self,
        *,
        target: str,
        source_key: str = "app_file",
        source_type: str = "file",
        inspect_path_prefixes: list[str] | None = None,
        project_name: str = "landingpage",
        project_summary: str = "Temporary landingpage-style project for collection tests.",
    ) -> Path:
        manifest_path = self.tmp_path / f"{project_name}.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "project_key": project_name,
                    "project_summary": project_summary,
                    "sources": [
                        {
                            "source_key": source_key,
                            "source_type": source_type,
                            "target": target,
                            "description": "Temporary file-backed application logs.",
                            "required": True,
                            "parser_type": "plain_text",
                            "normalization_profile": "app_logs",
                            "retention_class": "short",
                            "default_noise_profile": "app_noise",
                            "inspect_path_prefixes": inspect_path_prefixes or [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return manifest_path


@dataclass(slots=True)
class CollectLogsRequestFactory:
    """Build JSON-RPC payloads for the `collect_logs` tool."""

    def create(
        self,
        *,
        request_id: str = "collect-1",
        project_name: str = "landingpage",
        source_keys: list[str] | None = None,
        workspace: str = "workflow",
        session_id: str | None = None,
        tail_lines: int | None = 200,
        timestamps: bool = False,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "project_name": project_name,
            "source_keys": source_keys or [],
            "workspace": workspace,
            "timestamps": timestamps,
        }
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": "collect_logs",
                "arguments": arguments,
            },
        }
        if tail_lines is not None:
            arguments["tail_lines"] = tail_lines
        if session_id is not None:
            arguments["session_id"] = session_id
        if since is not None:
            arguments["since"] = since
        if until is not None:
            arguments["until"] = until
        return payload


@pytest.fixture
def settings_fixture() -> Settings:
    return Settings(
        ENVIRONMENT="dev",
        HOST="127.0.0.1",
        PORT=8001,
        LOG_LEVEL="INFO",
        LOG_FORMAT="text",
        JWT_ALGORITHM="HS256",
        JWT_SHARED_SECRET="change-me-local-dev-secret",
        JWT_ISSUER="mcp-log-server-dev",
        JWT_AUDIENCE="mcp-log-server",
        JWT_EXPIRATION_SECONDS=86400,
        MANIFEST_PATH=Path(__file__).resolve().parents[2] / "src/manifests/landingpage.json",
        MCP_PATH="/mcp",
        MCP_STATELESS_HTTP=True,
        MCP_JSON_RESPONSE=True,
    )


@pytest.fixture
def file_source_manifest_factory(tmp_path: Path) -> FileSourceManifestFactory:
    return FileSourceManifestFactory(tmp_path=tmp_path)


@pytest.fixture
def collect_logs_request_factory() -> CollectLogsRequestFactory:
    return CollectLogsRequestFactory()


@pytest.fixture
def create_test_jwt_token(settings_fixture: Settings) -> Callable[[str, list[str], str], str]:
    def build_token(subject: str, scopes: list[str], client_id: str) -> str:
        now = int(time.time())
        signing_key = OctKey.import_key(settings_fixture.JWT_SHARED_SECRET)
        header = {"alg": settings_fixture.JWT_ALGORITHM, "typ": "JWT"}
        payload = {
            "iss": settings_fixture.JWT_ISSUER,
            "aud": settings_fixture.JWT_AUDIENCE,
            "iat": now,
            "exp": now + settings_fixture.JWT_EXPIRATION_SECONDS,
            "sub": subject,
            "client_id": client_id,
            "project_key": "landingpage",
            "scope": " ".join(scopes),
        }
        return jwt.encode(
            header,
            payload,
            signing_key,
            algorithms=[settings_fixture.JWT_ALGORITHM],
        )

    return build_token


@pytest.fixture
def api_client(settings_fixture: Settings) -> Generator[TestClient]:
    app = create_application(auth_provider=build_auth_provider(settings_fixture))
    asgi_app = app.http_app(
        path=settings_fixture.MCP_PATH,
        json_response=settings_fixture.MCP_JSON_RESPONSE,
        stateless_http=settings_fixture.MCP_STATELESS_HTTP,
    )
    with TestClient(asgi_app) as client:
        yield client


@pytest.fixture
def jsonrpc(
    api_client: TestClient,
    settings_fixture: Settings,
) -> JsonRpcFixture:
    return JsonRpcFixture(
        client=JsonRpcClient(
            api_client=api_client,
            mcp_path=settings_fixture.MCP_PATH,
        )
    )
