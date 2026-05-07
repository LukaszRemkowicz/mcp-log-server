from __future__ import annotations

import shutil
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, overload

import httpx
import pytest
from fastmcp.server.auth import AccessToken
from joserfc import jwt
from joserfc.jwk import OctKey
from starlette.testclient import TestClient

from app import create_application
from auth.auth_provider import build_auth_provider
from auth.scopes import LOGS_COLLECT_SCOPE, PROJECTS_READ_SCOPE
from conf import set_settings, settings
from settings import Settings

JwtOverrides = dict[str, Any] | None
AccessTokenClaims = dict[str, Any] | None
TEST_FIXTURES_ROOT = Path(__file__).parent / "fixtures"
TEST_MANIFESTS_DIR = TEST_FIXTURES_ROOT / "manifests"
TEST_FILE_SOURCE_ROOT = TEST_FIXTURES_ROOT / "logs"

set_settings(
    settings.model_copy(
        update={
            "MANIFEST_PATH": TEST_MANIFESTS_DIR,
            "FILE_SOURCE_ROOT": TEST_FILE_SOURCE_ROOT,
        }
    )
)


class CustomJwtToken(Protocol):
    """Callable fixture type for creating test JWTs with optional claim overrides."""

    @overload
    def __call__(self, subject: str, scopes: list[str], client_id: str) -> str: ...

    @overload
    def __call__(
        self,
        subject: str,
        scopes: list[str],
        client_id: str,
        overrides: JwtOverrides,
    ) -> str: ...

    def __call__(
        self,
        subject: str,
        scopes: list[str],
        client_id: str,
        overrides: JwtOverrides = None,
    ) -> str: ...


class CustomAccessToken(Protocol):
    """Callable fixture type for creating direct FastMCP access tokens."""

    def __call__(
        self,
        subject: str,
        scopes: list[str],
        client_id: str,
        claims: AccessTokenClaims = None,
    ) -> AccessToken: ...


class FakeDockerExecResult:
    """Small Docker SDK exec result fake with UTF-8 encoded output."""

    def __init__(self, *, exit_code: int = 0, output: str = "") -> None:
        self.exit_code = exit_code
        self.output = output.encode("utf-8")


class FakeDockerClient:
    """Small Docker SDK fake for container log and inspection tests."""

    containers: FakeDockerClient

    def __init__(self) -> None:
        self.containers = self
        self.outputs_by_command: dict[tuple[str, ...], str] = {}
        self.commands: list[list[str]] = []
        self.captured_logs_kwargs: dict[str, object] = {}
        self.logs_exception: Exception | None = None

    def get(self, container_name: str) -> FakeDockerClient:
        assert container_name == "backend-container"
        return self

    def exec_run(
        self,
        command: list[str],
        stdout: bool = True,
        stderr: bool = True,
    ) -> FakeDockerExecResult:
        assert stdout is True
        assert stderr is True
        self.commands.append(command)
        command_key = tuple(command)
        return FakeDockerExecResult(output=self.outputs_by_command[command_key])

    def logs(self, **kwargs: object):
        if self.logs_exception is not None:
            raise self.logs_exception
        self.captured_logs_kwargs.update(kwargs)
        yield b"log line 1\n"
        yield b"log line 2\n"


@contextmanager
def override_settings(
    **updates: object,
) -> Generator[Settings]:
    """Temporarily patch selected shared app settings for tests."""

    previous_settings = settings.model_copy()
    effective_settings = previous_settings.model_copy(update=updates)

    set_settings(effective_settings)
    try:
        yield effective_settings
    finally:
        set_settings(previous_settings)


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


def build_collect_logs_request(
    *,
    request_id: str = "collect-1",
    project_names: list[str] | None = None,
    source_keys: list[str] | None = None,
    workspace: str = "workflow",
    session_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """Build a JSON-RPC payload for the `collect_logs` tool."""

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


@dataclass(slots=True)
class FileBackedProjectContext:
    """API test paths for the single file-backed landingpage manifest scenario."""

    logs_dir: Path
    manifests_dir: Path
    file_source_root: Path


@dataclass(slots=True)
class MultiProjectCollectContext:
    """API test paths for the multi-project manifest scenario."""

    logs_dir: Path
    manifests_dir: Path
    file_source_root: Path


@pytest.fixture
def custom_access_token() -> CustomAccessToken:
    """Return a builder for direct FastMCP access tokens with custom claims."""

    def build_token(
        subject: str,
        scopes: list[str],
        client_id: str,
        claims: AccessTokenClaims = None,
    ) -> AccessToken:
        effective_claims = {"sub": subject}
        effective_claims.update(claims or {})
        return AccessToken(
            token=f"{client_id}-test-token",
            client_id=client_id,
            scopes=scopes,
            claims=effective_claims,
        )

    return build_token


@pytest.fixture
def valid_access_token(custom_access_token: CustomAccessToken) -> AccessToken:
    """Return the common direct-tool token used by most service/tool tests."""

    return custom_access_token(
        "workflow-agent",
        [LOGS_COLLECT_SCOPE],
        "workflow-agent",
        {"allowed_projects": ["landingpage"]},
    )


@pytest.fixture
def fake_docker_client() -> FakeDockerClient:
    """Return a reusable fake Docker SDK client for tests."""

    return FakeDockerClient()


def copy_mutable_log_fixture_root(tmp_path: Path) -> Path:
    """Copy manifest and log fixtures for tests that rewrite source log files."""

    fixture_root = tmp_path / "fixtures"
    shutil.copytree(settings.MANIFEST_PATH, fixture_root / "manifests")
    shutil.copytree(settings.file_source_root, fixture_root / "logs")
    return fixture_root


@pytest.fixture
def container_manifests_dir() -> Path:
    """Return the reusable container-inspection manifest scenario."""

    return settings.MANIFEST_PATH / "container"


@pytest.fixture
def custom_jwt_token() -> CustomJwtToken:
    def build_token(
        subject: str,
        scopes: list[str],
        client_id: str,
        overrides: dict[str, Any] | None = None,
    ) -> str:
        now = int(time.time())
        effective_overrides = dict(overrides or {})
        signing_secret = effective_overrides.pop(
            "signing_secret",
            settings.JWT_SHARED_SECRET,
        )
        signing_key = OctKey.import_key(signing_secret)
        header = {"alg": settings.JWT_ALGORITHM, "typ": "JWT"}
        payload = {
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "iat": now,
            "exp": now + settings.JWT_EXPIRATION_SECONDS,
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
            algorithms=[settings.JWT_ALGORITHM],
        )

    return build_token


@pytest.fixture
def valid_jwt_token(custom_jwt_token: CustomJwtToken) -> str:
    """Return the common workflow-agent JWT used by most API tests."""

    return custom_jwt_token(
        "workflow-agent",
        [LOGS_COLLECT_SCOPE, PROJECTS_READ_SCOPE],
        "workflow-agent",
    )


@pytest.fixture
def jsonrpc() -> Generator[JsonRpcClient]:
    app = create_application(auth_provider=build_auth_provider(settings))
    asgi_app = app.http_app(
        path=settings.MCP_PATH,
        json_response=settings.MCP_JSON_RESPONSE,
        stateless_http=settings.MCP_STATELESS_HTTP,
    )
    with TestClient(asgi_app) as client:
        yield JsonRpcClient(
            api_client=client,
            mcp_path=settings.MCP_PATH,
        )


@pytest.fixture
def file_backed_project_context(
    tmp_path: Path,
) -> FileBackedProjectContext:
    """Create file-backed project paths for API tests."""

    logs_dir: Path = tmp_path / "collected-logs"

    return FileBackedProjectContext(
        logs_dir=logs_dir,
        manifests_dir=settings.MANIFEST_PATH,
        file_source_root=settings.file_source_root,
    )


@pytest.fixture
def multi_project_collect_context(
    tmp_path: Path,
) -> MultiProjectCollectContext:
    """Return paths for multi-project collection tests."""

    logs_dir: Path = tmp_path / "collected-logs"
    return MultiProjectCollectContext(
        logs_dir=logs_dir,
        manifests_dir=settings.MANIFEST_PATH,
        file_source_root=settings.file_source_root,
    )
