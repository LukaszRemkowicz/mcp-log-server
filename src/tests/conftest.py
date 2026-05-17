from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, overload

import httpx
import pytest
from fastmcp.server.auth import AccessToken
from joserfc import jwt
from joserfc.jwk import OctKey
from tortoise import Tortoise, connections

from app import create_application, mcp
from auth.auth_provider import build_auth_provider
from auth.mcp_caller_model import get_mcp_caller_model
from auth.scopes import LOGS_COLLECT_SCOPE, PROJECTS_READ_SCOPE
from conf import set_settings, settings
from core.types import LogWorkspace
from database.managers import DatabaseModel
from database.models import (
    AgentCall,
    AgentSession,
    CollectLogs,
    CollectLogsSource,
    McpCaller,
    ProjectManifest,
)
from database.schemas import ProjectManifestCreate, ProjectManifestUpdate
from database.services.project_manifests import ProjectManifestService as ProjectManifestDBService
from database.signals import register_database_signals
from manifests.loader import list_project_manifests
from manifests.models import Manifest
from settings import Settings

JwtOverrides = dict[str, Any] | None
AccessTokenClaims = dict[str, Any] | None
TEST_FIXTURES_ROOT = Path(__file__).parent / "fixtures"
TEST_MANIFESTS_DIR = TEST_FIXTURES_ROOT / "manifests"
TEST_FILE_SOURCE_ROOT = TEST_FIXTURES_ROOT / "logs"
INIT_DB_REQUIRED_MESSAGES = (
    "You need to run `aerich init-db` first",
    "You may need to run `aerich init-db` first",
)


@asynccontextmanager
async def _test_lifespan_without_database(_app: object) -> AsyncIterator[None]:
    """Run FastMCP HTTP lifespan without app-owned database startup/shutdown.

    API tests still need FastMCP's lifespan machinery because the HTTP
    transport initializes its request/session manager there. The test database
    fixture owns Tortoise setup, seeding, flushing, and shutdown, so the app's
    production DB lifespan must not run a second Tortoise lifecycle inside the
    test request client.
    """

    yield


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


def _default_client_type(client_id: str) -> str:
    """Return the default local JWT client type for one test client id."""

    client_types = {
        "workflow-agent": "workflow_agent",
        "codex-agent": "codex",
    }
    return client_types.get(client_id, client_id)


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
        self.attrs: dict[str, object] = {}

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
) -> Iterator[Settings]:
    """Temporarily patch selected shared app settings for tests."""

    previous_settings = settings.model_copy()
    effective_settings = previous_settings.model_copy(update=updates)

    set_settings(effective_settings)
    try:
        yield effective_settings
    finally:
        set_settings(previous_settings)


def _test_manifest_sources_payload(manifest: Manifest) -> list[dict[str, Any]]:
    """Return test manifest sources with fixture file targets valid in this runtime."""

    sources: list[dict[str, Any]] = []
    for source in manifest.sources:
        source_payload = source.model_dump(mode="json")
        if source_payload["source_type"] == "file":
            source_target = Path(source_payload["target"])
            try:
                relative_source_path = source_target.relative_to("/app/src/tests/fixtures/logs")
            except ValueError:
                relative_source_path = None
            if relative_source_path is not None:
                source_payload["target"] = (TEST_FILE_SOURCE_ROOT / relative_source_path).as_posix()
        sources.append(source_payload)
    return sources


def runtime_test_manifest(manifest: Manifest) -> Manifest:
    """Return a test manifest whose file sources exist in this runtime."""

    return Manifest.model_validate(
        {
            **manifest.model_dump(mode="json"),
            "sources": _test_manifest_sources_payload(manifest),
        }
    )


def _run_aerich_for_tests(*args: str) -> subprocess.CompletedProcess[str]:
    """Run one Aerich command against the current test settings database."""

    return subprocess.run(
        ["aerich", *args],
        capture_output=True,
        check=False,
        text=True,
    )


def _raise_migration_error(result: subprocess.CompletedProcess[str]) -> None:
    """Raise a readable pytest startup error for failed test migrations."""

    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    raise RuntimeError(f"Failed to prepare test database migrations:\n{output}")


def pytest_sessionstart(session: pytest.Session) -> None:  # noqa: ARG001
    """Apply database migrations before DB-backed test fixtures touch tables."""

    result = _run_aerich_for_tests("upgrade")
    output = f"{result.stdout or ''}{result.stderr or ''}"
    if result.returncode == 0:
        return
    if any(message in output for message in INIT_DB_REQUIRED_MESSAGES):
        init_result = _run_aerich_for_tests("init-db")
        if init_result.returncode != 0:
            _raise_migration_error(init_result)
        return
    _raise_migration_error(result)


async def _flush_database_tables() -> None:
    """Delete all rows from registered Tortoise app model tables."""

    ordered_models: list[type[DatabaseModel]] = [
        CollectLogsSource,
        CollectLogs,
        AgentCall,
        AgentSession,
        McpCaller,
    ]
    for model in ordered_models:
        await model.all().delete()
    for registered_model in Tortoise.apps["models"].values():
        if registered_model in ordered_models or registered_model.__name__ == "Aerich":
            continue
        await registered_model.all().delete()


async def _clear_project_manifest_cache() -> None:
    """Clear manifest caches that can otherwise cross pytest event loops."""

    await ProjectManifest.clear_cache()


async def _initialize_test_database() -> None:
    """Initialize Tortoise for a test database fixture."""

    await Tortoise.init(
        db_url=settings.db,
        modules={"models": ["database.models"]},
    )
    register_database_signals()


async def _close_test_database() -> None:
    """Close and reset Tortoise after a test database fixture."""

    await connections.close_all(discard=True)
    connections._clear_storage()  # noqa Access to a protected member of a class
    await Tortoise._reset_apps()  # noqa Access to a protected member of a class


@pytest.fixture
async def db() -> AsyncIterator[None]:
    """Provide Django-style database setup, manifest seed, and flush for each test."""

    await _clear_project_manifest_cache()
    await _initialize_test_database()
    await _flush_database_tables()
    await _create_callers()
    await _seed_project_manifests()
    try:
        yield
    finally:
        await _clear_project_manifest_cache()
        await _flush_database_tables()
        await _close_test_database()


@dataclass(slots=True)
class JsonRpcClient:
    """Small test helper for authenticated JSON-RPC POST calls."""

    api_client: httpx.AsyncClient
    mcp_path: str

    async def post(
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

        return await self.api_client.post(self.mcp_path, headers=headers, json=data)


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


async def _seed_project_manifests(
    manifests_dir: Path = TEST_MANIFESTS_DIR,
) -> None:
    """Persist current test manifests for MCP tools that now read manifests from DB."""

    service = ProjectManifestDBService()
    for manifest in list_project_manifests(manifests_dir):
        sources = _test_manifest_sources_payload(manifest)
        if await service.exists(manifest.project_key):
            existing = await service.get(manifest.project_key)
            await service.update(
                ProjectManifestUpdate(
                    pk=existing.id,
                    project_summary=manifest.project_summary,
                    static_asset_paths=manifest.static_asset_paths,
                    static_asset_extensions=manifest.static_asset_extensions,
                    sources=sources,
                )
            )
            continue
        await service.create(
            ProjectManifestCreate(
                project_key=manifest.project_key,
                project_summary=manifest.project_summary,
                static_asset_paths=manifest.static_asset_paths,
                static_asset_extensions=manifest.static_asset_extensions,
                sources=sources,
            )
        )


async def _create_callers() -> None:
    """Create default authenticated MCP callers for tests."""

    default_mcp_clients: tuple[tuple[str, str, LogWorkspace, list[str]], ...] = (
        (
            "workflow-agent",
            "workflow_agent",
            LogWorkspace.WORKFLOW,
            ["dockerpage", "landingpage", "shop"],
        ),
        ("codex-agent", "codex", LogWorkspace.WORKFLOW, ["dockerpage", "landingpage", "shop"]),
        ("codex-agent", "codex", LogWorkspace.SESSION, ["dockerpage", "landingpage", "shop"]),
        ("agent", "agent", LogWorkspace.WORKFLOW, ["dockerpage", "landingpage", "shop"]),
        ("agent", "agent", LogWorkspace.SESSION, ["dockerpage", "landingpage", "shop"]),
        ("codex-client", "codex", LogWorkspace.SESSION, ["landingpage"]),
        ("workflow-client", "workflow_agent", LogWorkspace.WORKFLOW, ["landingpage"]),
        ("status-no-project-client", "codex", LogWorkspace.WORKFLOW, []),
        ("caller-context-landingpage", "codex", LogWorkspace.WORKFLOW, ["landingpage"]),
        ("caller-context-dockerpage", "codex", LogWorkspace.WORKFLOW, ["dockerpage"]),
        ("caller-context-dockerpage", "codex", LogWorkspace.SESSION, ["dockerpage"]),
        ("all-project-workflow-client", "workflow_agent", LogWorkspace.WORKFLOW, ["all"]),
        (
            "limited-workflow-client",
            "workflow_agent",
            LogWorkspace.WORKFLOW,
            ["landingpage", "shop"],
        ),
    )
    for client_id, client_type, workspace, allowed_projects in default_mcp_clients:
        caller_model = get_mcp_caller_model()
        exists = (
            await caller_model.objects.filter(
                client_id=client_id,
                client_type=client_type,
                workspace=workspace,
            )
            .limit(1)
            .count()
        )
        if exists:
            continue
        await caller_model.objects.create(
            client_id=client_id,
            client_type=client_type,
            workspace=workspace,
            allowed_projects=allowed_projects,
        )


@dataclass(slots=True)
class FileBackedProjectContext:
    """API test paths for the single file-backed landingpage manifest scenario."""

    logs_dir: Path


@dataclass(slots=True)
class MultiProjectCollectContext:
    """API test paths for the multi-project manifest scenario."""

    logs_dir: Path


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


def copy_manifest_and_log_fixtures(tmp_path: Path) -> Path:
    """Copy manifest/log fixtures and point file sources at the copied logs.

    Some tests rewrite fixture log content to exercise collection and grep
    behavior. Those tests must not mutate repository fixtures, and the seeded
    project manifests must point at the copied files instead of the original
    `/app/src/tests/fixtures/logs` paths.
    """

    fixture_root = tmp_path / "fixtures"
    shutil.copytree(TEST_MANIFESTS_DIR, fixture_root / "manifests")
    shutil.copytree(TEST_FILE_SOURCE_ROOT, fixture_root / "logs")
    for manifest_path in (fixture_root / "manifests").glob("*.json"):
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        for source in manifest_data.get("sources", []):
            if source.get("source_type") != "file":
                continue
            source_target = Path(source["target"])
            try:
                relative_source_path = source_target.relative_to("/app/src/tests/fixtures/logs")
            except ValueError:
                relative_source_path = Path(*source_target.parts[-2:])
            source["target"] = (fixture_root / "logs" / relative_source_path).as_posix()
        manifest_path.write_text(
            json.dumps(manifest_data, indent=2) + "\n",
            encoding="utf-8",
        )
    return fixture_root


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
            "client_type": _default_client_type(client_id),
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
        {"client_type": "workflow_agent"},
    )


@pytest.fixture
async def jsonrpc(
    db: None,  # noqa: ARG001
) -> AsyncIterator[JsonRpcClient]:
    """Return a JSON-RPC client that exercises the real FastMCP HTTP path.

    The async ASGI client keeps request handling in the same event loop as the
    async database fixture, so Tortoise and asyncpg share one straightforward
    setup path.
    """

    app = create_application(auth_provider=build_auth_provider(settings))
    asgi_app = app.http_app(
        path=settings.MCP_PATH,
        json_response=settings.MCP_JSON_RESPONSE,
        stateless_http=settings.MCP_STATELESS_HTTP,
    )
    previous_lifespan = mcp._lifespan  # noqa: SLF001
    mcp._lifespan = _test_lifespan_without_database  # noqa: SLF001
    try:
        async with asgi_app.router.lifespan_context(asgi_app):
            transport = httpx.ASGITransport(app=asgi_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                yield JsonRpcClient(
                    api_client=client,
                    mcp_path=settings.MCP_PATH,
                )
    finally:
        mcp._lifespan = previous_lifespan  # noqa: SLF001


@pytest.fixture
def file_backed_project_context(
    tmp_path: Path,
) -> FileBackedProjectContext:
    """Create file-backed project paths for API tests."""

    logs_dir: Path = tmp_path / "collected-logs"

    return FileBackedProjectContext(
        logs_dir=logs_dir,
    )


@pytest.fixture
def multi_project_collect_context(
    tmp_path: Path,
) -> MultiProjectCollectContext:
    """Return paths for multi-project collection tests."""

    logs_dir: Path = tmp_path / "collected-logs"
    return MultiProjectCollectContext(
        logs_dir=logs_dir,
    )
