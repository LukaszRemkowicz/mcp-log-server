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

import conf
from app import create_application
from auth.auth_provider import build_auth_provider
from middleware import audit as audit_middleware
from services import log_collection as log_collection_service
from services import log_snapshots as log_snapshot_service
from services import project_manifest as project_manifest_service
from settings import Settings
from tools import collection as collection_tools
from tools import container_inspection as container_inspection_tools
from tools import system as system_tools


@contextmanager
def override_settings(
    settings: Settings | None = None,
    /,
    **updates: object,
) -> Generator[Settings]:
    """Temporarily replace shared app settings for tests.

    Usage styles:

    - `with override_settings(custom_settings):`
      replace the full settings object
    - `with override_settings(MANIFEST_PATH=manifest_path):`
      patch selected fields on top of the current settings object
    """

    effective_settings = (
        settings.model_copy(update=updates)
        if settings is not None
        else conf.settings.model_copy(update=updates)
    )

    with (
        patch.object(conf, "settings", effective_settings),
        patch.object(audit_middleware, "settings", effective_settings),
        patch.object(log_collection_service, "settings", effective_settings),
        patch.object(log_snapshot_service, "settings", effective_settings),
        patch.object(project_manifest_service, "settings", effective_settings),
        patch.object(collection_tools, "settings", effective_settings),
        patch.object(container_inspection_tools, "settings", effective_settings),
        patch.object(system_tools, "settings", effective_settings),
    ):
        yield effective_settings


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

        with override_settings(settings) as effective_settings:
            app = create_application(auth_provider=build_auth_provider(effective_settings))
            asgi_app = app.http_app(
                path=effective_settings.MCP_PATH,
                json_response=effective_settings.MCP_JSON_RESPONSE,
                stateless_http=effective_settings.MCP_STATELESS_HTTP,
            )
            with TestClient(asgi_app) as api_client:
                yield JsonRpcClient(
                    api_client=api_client,
                    mcp_path=effective_settings.MCP_PATH,
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
        project_names: list[str] | None = None,
        source_keys: list[str] | None = None,
        workspace: str = "workflow",
        session_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "source_keys": ["all"] if source_keys is None else source_keys,
            "workspace": workspace,
            "project_names": ["landingpage"] if project_names is None else project_names,
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
def create_test_jwt_token(
    settings_fixture: Settings,
) -> Callable[[str, list[str], str, dict[str, Any] | None], str]:
    def build_token(
        subject: str,
        scopes: list[str],
        client_id: str,
        overrides: dict[str, Any] | None = None,
    ) -> str:
        now = int(time.time())
        effective_overrides = overrides or {}
        signing_secret = effective_overrides.pop(
            "signing_secret",
            settings_fixture.JWT_SHARED_SECRET,
        )
        signing_key = OctKey.import_key(signing_secret)
        header = {"alg": settings_fixture.JWT_ALGORITHM, "typ": "JWT"}
        payload = {
            "iss": settings_fixture.JWT_ISSUER,
            "aud": settings_fixture.JWT_AUDIENCE,
            "iat": now,
            "exp": now + settings_fixture.JWT_EXPIRATION_SECONDS,
            "sub": subject,
            "client_id": client_id,
            "allowed_projects": ["landingpage"],
            "scope": " ".join(scopes),
        }
        payload.update(effective_overrides)
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
