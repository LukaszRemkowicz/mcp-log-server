"""Tests for project manifest upload commands."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
import typer
from typer.testing import CliRunner

from scripts.commands import upload_project_manifest as upload_command
from scripts.docker_commands import DockerCommandResult
from scripts.main import app
from settings import Settings

runner = CliRunner()


class FakeProjectManifestService:
    """Capture uploaded manifests without touching the database."""

    def __init__(self) -> None:
        self.uploaded_project_keys: list[str] = []
        self.updated_project_keys: list[str] = []
        self.existing_project_keys: set[str] = set()

    async def exists(self, project_key):
        """Return whether the fake manifest row exists."""

        return project_key in self.existing_project_keys

    async def create(self, payload):
        """Pretend to create one manifest."""

        self.existing_project_keys.add(payload.project_key)
        self.uploaded_project_keys.append(payload.project_key)
        return SimpleNamespace(id=uuid4())

    @staticmethod
    async def get(project_key):
        """Return a fake existing row."""

        return SimpleNamespace(id=uuid4(), project_key=project_key)

    async def update(self, payload):
        """Pretend to update one manifest."""

        self.updated_project_keys.append(str(payload.pk))
        return SimpleNamespace(id=payload.pk)


@asynccontextmanager
async def fake_database_context() -> AsyncIterator[None]:
    """Avoid opening real DB connections in command tests."""

    yield


def _write_manifest(manifests_dir, project_key: str) -> None:
    """Write one valid manifest fixture."""

    (manifests_dir / f"{project_key}.json").write_text(
        f"""{{
  "project_key": "{project_key}",
  "project_summary": "Test project.",
  "static_asset_paths": ["/static/"],
  "static_asset_extensions": [".css"],
  "sources": [
    {{
      "source_key": "backend",
      "source_type": "docker",
      "target": "{project_key}-backend",
      "description": "Backend logs.",
      "parser_type": "python_json",
      "normalization_profile": "backend_app",
      "retention_class": "hot"
    }}
  ]
}}""",
        encoding="utf-8",
    )


def test_upload_project_manifest_internal_uploads_one_project(monkeypatch, tmp_path) -> None:
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    _write_manifest(manifests_dir, "landingpage")
    fake_service = FakeProjectManifestService()

    monkeypatch.setattr(upload_command, "settings", Settings(MANIFEST_PATH=manifests_dir))
    monkeypatch.setattr(upload_command, "ProjectManifestService", lambda: fake_service)
    monkeypatch.setattr(upload_command, "database_context", fake_database_context)

    result = runner.invoke(app, ["upload-project-manifest-internal", "landingpage"])

    assert result.exit_code == 0
    assert fake_service.uploaded_project_keys == ["landingpage"]
    assert "Created project manifest landingpage (sources: 1, row_id:" in result.output
    assert "Upload summary: created 1, already existing 0, total 1." in result.output


def test_upload_project_manifest_internal_uploads_all_projects(monkeypatch, tmp_path) -> None:
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    _write_manifest(manifests_dir, "zeta")
    _write_manifest(manifests_dir, "alpha")
    fake_service = FakeProjectManifestService()

    monkeypatch.setattr(upload_command, "settings", Settings(MANIFEST_PATH=manifests_dir))
    monkeypatch.setattr(upload_command, "ProjectManifestService", lambda: fake_service)
    monkeypatch.setattr(upload_command, "database_context", fake_database_context)

    result = runner.invoke(app, ["upload-project-manifest-internal", "--all"])

    assert result.exit_code == 0
    assert fake_service.uploaded_project_keys == ["alpha", "zeta"]
    assert "Upload summary: created 2, already existing 0, total 2." in result.output


def test_upload_project_manifest_internal_reports_existing_without_update(
    monkeypatch,
    tmp_path,
) -> None:
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    _write_manifest(manifests_dir, "landingpage")
    fake_service = FakeProjectManifestService()
    fake_service.existing_project_keys.add("landingpage")

    monkeypatch.setattr(upload_command, "settings", Settings(MANIFEST_PATH=manifests_dir))
    monkeypatch.setattr(upload_command, "ProjectManifestService", lambda: fake_service)
    monkeypatch.setattr(upload_command, "database_context", fake_database_context)

    result = runner.invoke(app, ["upload-project-manifest-internal", "landingpage"])

    assert result.exit_code == 0
    assert fake_service.uploaded_project_keys == []
    assert fake_service.updated_project_keys == []
    assert "Project manifest landingpage already exists and was not changed." in result.output
    assert "uv run commands update-project-manifest --project landingpage" in result.output
    assert "Upload summary: created 0, already existing 1, total 1." in result.output


def test_update_project_manifest_internal_updates_existing_project(
    monkeypatch,
    tmp_path,
) -> None:
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    _write_manifest(manifests_dir, "landingpage")
    fake_service = FakeProjectManifestService()
    fake_service.existing_project_keys.add("landingpage")

    monkeypatch.setattr(upload_command, "settings", Settings(MANIFEST_PATH=manifests_dir))
    monkeypatch.setattr(upload_command, "ProjectManifestService", lambda: fake_service)
    monkeypatch.setattr(upload_command, "database_context", fake_database_context)

    result = runner.invoke(app, ["update-project-manifest-internal", "--project", "landingpage"])

    assert result.exit_code == 0
    assert len(fake_service.updated_project_keys) == 1
    assert "Updated project manifest landingpage (sources: 1, row_id:" in result.output


def test_update_project_manifest_internal_reports_missing_project(
    monkeypatch,
    tmp_path,
) -> None:
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    _write_manifest(manifests_dir, "landingpage")
    fake_service = FakeProjectManifestService()

    monkeypatch.setattr(upload_command, "settings", Settings(MANIFEST_PATH=manifests_dir))
    monkeypatch.setattr(upload_command, "ProjectManifestService", lambda: fake_service)
    monkeypatch.setattr(upload_command, "database_context", fake_database_context)

    result = runner.invoke(app, ["update-project-manifest-internal", "--project", "landingpage"])

    assert result.exit_code == 0
    assert fake_service.updated_project_keys == []
    assert "Project manifest landingpage does not exist." in result.output
    assert "uv run commands upload-project-manifest landingpage" in result.output


def test_upload_project_manifest_command_requires_selection() -> None:
    result = runner.invoke(app, ["upload-project-manifest"])

    assert result.exit_code == 2
    with pytest.raises(typer.BadParameter, match="Provide PROJECT_NAME or use --all."):
        upload_command.upload_project_manifest()


def test_upload_project_manifest_command_rejects_project_and_all() -> None:
    result = runner.invoke(app, ["upload-project-manifest", "landingpage", "--all"])

    assert result.exit_code == 2
    with pytest.raises(
        typer.BadParameter,
        match="Use either PROJECT_NAME or --all, not both.",
    ):
        upload_command.upload_project_manifest(project_name="landingpage", all_projects=True)


def test_upload_project_manifest_command_runs_inside_app_container(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeDockerCommandService:
        @staticmethod
        def run_compose_service_command(**kwargs):
            calls.append(kwargs)
            return DockerCommandResult(
                exit_code=0,
                output="Uploaded project manifest landingpage\n",
            )

    monkeypatch.setattr(upload_command, "DockerCommandService", FakeDockerCommandService)

    result = runner.invoke(app, ["upload-project-manifest", "landingpage"])

    assert result.exit_code == 0
    assert result.output == "Uploaded project manifest landingpage\n"
    assert calls == [
        {
            "project_name": "mcp-log-server",
            "service_name": "app",
            "command": [
                "uv",
                "run",
                "python",
                "-m",
                "scripts.main",
                "upload-project-manifest-internal",
                "landingpage",
            ],
        }
    ]


def test_upload_project_manifest_command_passes_all_to_app_container(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeDockerCommandService:
        @staticmethod
        def run_compose_service_command(**kwargs):
            calls.append(kwargs)
            return DockerCommandResult(exit_code=0, output="")

    monkeypatch.setattr(upload_command, "DockerCommandService", FakeDockerCommandService)

    result = runner.invoke(app, ["upload-project-manifest", "--all"])

    assert result.exit_code == 0
    assert calls[0]["command"] == [
        "uv",
        "run",
        "python",
        "-m",
        "scripts.main",
        "upload-project-manifest-internal",
        "--all",
    ]


def test_update_project_manifest_command_runs_inside_app_container(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeDockerCommandService:
        @staticmethod
        def run_compose_service_command(**kwargs):
            calls.append(kwargs)
            return DockerCommandResult(
                exit_code=0,
                output="Updated project manifest landingpage\n",
            )

    monkeypatch.setattr(upload_command, "DockerCommandService", FakeDockerCommandService)

    result = runner.invoke(app, ["update-project-manifest", "--project", "landingpage"])

    assert result.exit_code == 0
    assert result.output == "Updated project manifest landingpage\n"
    assert calls[0]["command"] == [
        "uv",
        "run",
        "python",
        "-m",
        "scripts.main",
        "update-project-manifest-internal",
        "--project",
        "landingpage",
    ]
