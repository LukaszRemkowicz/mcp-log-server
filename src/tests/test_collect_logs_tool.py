from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp.server.auth import AccessToken

import decorators
import tools.collection as collection_tools
from conf import settings
from manifests.loader import list_project_manifests, load_project_manifest
from services.log_collection import LogCollectionService
from services.project_manifest import ProjectManifestContext
from tests.conftest import CustomAccessToken, override_settings
from tools.collection import collect_logs, list_projects
from tools.models import ProjectManifestList, ProjectManifestSummary


class FileBackedProjectManifestService:
    """Test double that keeps direct tool tests focused on tool behavior."""

    async def get(self, project_name: str):
        try:
            manifest = load_project_manifest(settings.manifests_dir, project_name)
        except FileNotFoundError:
            return None
        return ProjectManifestContext(manifest=manifest, project_name=project_name)

    async def get_or_error(self, project_name: str):
        result = await self.get(project_name)
        if result is not None:
            return result
        return collection_tools.ProjectManifestError(
            message=(
                f"Unknown project {project_name!r}. "
                "No persisted manifest was found for that project."
            )
        )

    async def all(self) -> ProjectManifestList:
        manifests = list_project_manifests(settings.manifests_dir)
        summaries = [
            ProjectManifestSummary(
                project_name=manifest.project_key,
                project_summary=manifest.project_summary,
                source_keys=[source.source_key for source in manifest.sources],
            )
            for manifest in manifests
        ]
        return ProjectManifestList.model_validate(summaries)

    get_manifest_source_keys = staticmethod(
        collection_tools.ProjectManifestService.get_manifest_source_keys
    )


def _run(coro):
    return asyncio.run(coro)


def _patch_manifest_services(monkeypatch) -> None:
    service = FileBackedProjectManifestService()
    monkeypatch.setattr(decorators, "project_manifest_service", service)
    monkeypatch.setattr(collection_tools, "manifest_service", service)
    monkeypatch.setattr(
        collection_tools,
        "collection_service",
        LogCollectionService(),
    )


def test_collect_logs_returns_agent_error_for_invalid_docker_time_filter(
    container_manifests_dir: Path,
    custom_access_token: CustomAccessToken,
    monkeypatch,
) -> None:
    _patch_manifest_services(monkeypatch)
    token = custom_access_token(
        "workflow-agent",
        ["logs.collect"],
        "workflow-agent",
        {"projects_access": "all"},
    )

    with override_settings(MANIFEST_PATH=container_manifests_dir):
        result = _run(
            collect_logs(
                project_names=["landingpage"],
                source_keys=["backend"],
                since="thirty-minutes",
                until=None,
                access_token=token,
            )
        )
    mcp_result = result.to_mcp_result()

    assert mcp_result.isError is True
    assert mcp_result.structuredContent["error_code"] == "invalid_docker_time_filter"
    assert mcp_result.structuredContent["retry_tips"] == [
        "Retry with since/until as ISO-8601, unix seconds, or a duration like 30m, 1h, or 1d.",
        "Omit since/until if you want the current default collection range.",
    ]


def test_collect_logs_returns_agent_error_for_unknown_project_without_middleware(
    custom_access_token: CustomAccessToken,
    monkeypatch,
) -> None:
    _patch_manifest_services(monkeypatch)
    token = custom_access_token(
        "workflow-agent",
        ["logs.collect"],
        "workflow-agent",
        {"projects_access": "all"},
    )

    with override_settings():
        result = _run(
            collect_logs(
                project_names=["other-project"],
                source_keys=None,
                since=None,
                until=None,
                access_token=token,
            )
        )
    mcp_result = result.to_mcp_result()

    assert mcp_result.isError is True
    assert mcp_result.structuredContent["error_code"] == "unknown_project"
    assert mcp_result.structuredContent["retry_tips"] == [
        "Call list_projects to discover the project_name values currently available.",
        "Retry with one of the listed project names.",
    ]


def test_collect_logs_returns_agent_error_for_missing_session_id(
    valid_access_token: AccessToken,
    monkeypatch,
) -> None:
    _patch_manifest_services(monkeypatch)
    with override_settings():
        result = _run(
            collect_logs(
                project_names=["landingpage"],
                source_keys=["app_file"],
                workspace="session",
                session_id=None,
                since=None,
                until=None,
                access_token=valid_access_token,
            )
        )
    mcp_result = result.to_mcp_result()

    assert mcp_result.isError is True
    assert mcp_result.structuredContent["error_code"] == "missing_session_id"


@pytest.mark.db
@pytest.mark.anyio
async def test_collect_logs_uses_accessible_projects_for_empty_project_names(
    tmp_path: Path,
    valid_access_token: AccessToken,
    monkeypatch,
) -> None:
    _patch_manifest_services(monkeypatch)
    with override_settings(LOGS_DIR=tmp_path / "collected-logs"):
        result = await collect_logs(
            project_names=[],
            source_keys=["app_file"],
            since=None,
            until=None,
            access_token=valid_access_token,
        )
    payload = result.structured_content

    assert payload is not None
    assert payload["projects"][0]["project_name"] == "landingpage"


def test_list_projects_returns_manifest_backed_project_inventory(
    custom_access_token: CustomAccessToken,
    monkeypatch,
) -> None:
    _patch_manifest_services(monkeypatch)
    token = custom_access_token(
        "workflow-agent",
        ["projects.read"],
        "workflow-agent",
        {"allowed_projects": ["landingpage"]},
    )
    with override_settings():
        result = _run(list_projects(access_token=token))

    assert any(item["project_name"] == "landingpage" for item in result)
    landingpage = next(item for item in result if item["project_name"] == "landingpage")
    assert landingpage["project_summary"] == "Landingpage project for analysis tests."
    assert "backend" in landingpage["source_keys"]


def test_list_projects_returns_multiple_manifest_backed_projects(
    custom_access_token: CustomAccessToken,
    monkeypatch,
) -> None:
    _patch_manifest_services(monkeypatch)
    token = custom_access_token(
        "workflow-agent",
        ["projects.read"],
        "workflow-agent",
        {"projects_access": "all"},
    )
    with override_settings():
        result = _run(list_projects(access_token=token))

    assert [item["project_name"] for item in result] == [
        "alpha",
        "beta",
        "landingpage",
        "other",
        "shop",
    ]


@pytest.mark.db
@pytest.mark.anyio
async def test_collect_logs_can_collect_multiple_projects_into_one_session(
    tmp_path: Path,
    custom_access_token: CustomAccessToken,
    monkeypatch,
) -> None:
    _patch_manifest_services(monkeypatch)
    token = custom_access_token(
        "codex-agent",
        ["logs.collect"],
        "codex-agent",
        {"projects_access": "all"},
    )
    logs_dir = tmp_path / "collected-logs"

    with override_settings(LOGS_DIR=logs_dir):
        result = await collect_logs(
            project_names=["alpha", "beta"],
            source_keys=["app_file"],
            workspace="session",
            session_id="c2ccab1b-b6cc-422c-95e2-f64b507eeb4f",
            access_token=token,
        )
    payload = result.structured_content
    assert payload is not None

    assert payload["workspace"] == "session"
    assert payload["session_id"] == "c2ccab1b-b6cc-422c-95e2-f64b507eeb4f"
    assert payload["requested_project_names"] == ["alpha", "beta"]
    assert [item["project_name"] for item in payload["projects"]] == ["alpha", "beta"]
    assert payload["projects"][0]["snapshot_dir"] == str(
        logs_dir / "sessions" / "c2ccab1b-b6cc-422c-95e2-f64b507eeb4f" / "alpha"
    )
    assert payload["projects"][1]["snapshot_dir"] == str(
        logs_dir / "sessions" / "c2ccab1b-b6cc-422c-95e2-f64b507eeb4f" / "beta"
    )
