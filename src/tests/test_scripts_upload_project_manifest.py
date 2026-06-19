"""Tests for project manifest upload commands."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
import typer
from typer.testing import CliRunner

from cli.commands import upload_project_manifest as upload_command
from cli.main import app
from database.schemas import ProjectManifestCreate, ProjectManifestUpdate

runner = CliRunner()


class FakeProjectManifestService:
    """Capture uploaded manifests without touching the database."""

    def __init__(self) -> None:
        self.uploaded_project_keys: list[str] = []
        self.updated_project_keys: list[str] = []
        self.created_payloads: list[ProjectManifestCreate] = []
        self.updated_payloads: list[ProjectManifestUpdate] = []
        self.existing_project_keys: set[str] = set()

    async def exists(self, project_key: str) -> bool:
        """Return whether the fake manifest row exists."""

        return project_key in self.existing_project_keys

    async def create(self, payload: ProjectManifestCreate) -> SimpleNamespace:
        """Pretend to create one manifest."""

        self.existing_project_keys.add(payload.project_key)
        self.uploaded_project_keys.append(payload.project_key)
        self.created_payloads.append(payload)
        return SimpleNamespace(id=uuid4())

    @staticmethod
    async def get(project_key: str) -> SimpleNamespace:
        """Return a fake existing row."""

        return SimpleNamespace(id=uuid4(), project_key=project_key)

    async def update(self, payload: ProjectManifestUpdate) -> SimpleNamespace:
        """Pretend to update one manifest."""

        self.updated_project_keys.append(str(payload.pk))
        self.updated_payloads.append(payload)
        return SimpleNamespace(id=payload.pk)


@asynccontextmanager
async def fake_database_lifespan(_app: object = None) -> AsyncIterator[None]:
    """Avoid opening real DB connections in command tests."""

    yield


@pytest.fixture(autouse=True)
def _fake_database_lifespan(monkeypatch) -> None:
    monkeypatch.setattr("decorators.database_lifespan", fake_database_lifespan)


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
      "compose_project": "{project_key}",
      "compose_service": "backend",
      "description": "Backend logs.",
      "parser_type": "python_json",
      "normalization_profile": "backend_app",
      "retention_class": "hot"
    }}
  ]
}}""",
        encoding="utf-8",
    )


def _write_manifest_with_deployment(manifests_dir, project_key: str) -> None:
    """Write one valid manifest fixture with deployment metadata."""

    (manifests_dir / f"{project_key}.json").write_text(
        f"""{{
  "project_key": "{project_key}",
  "project_summary": "Test project.",
  "static_asset_paths": ["/static/"],
  "static_asset_extensions": [".css"],
  "deployment": {{
    "compose_files": ["/opt/{project_key}/docker-compose.prod.yml"],
    "current_tag_path": "/var/lib/{project_key}/prod/current_tag",
    "expected_image_repositories": {{
      "backend": "{project_key}-backend"
    }}
  }},
  "sources": [
    {{
      "source_key": "backend",
      "source_type": "docker",
      "target": "{project_key}-backend",
      "compose_project": "{project_key}",
      "compose_service": "backend",
      "description": "Backend logs.",
      "parser_type": "python_json",
      "normalization_profile": "backend_app",
      "retention_class": "hot"
    }}
  ]
}}""",
        encoding="utf-8",
    )


def test_upload_project_manifest_uploads_one_project(monkeypatch, tmp_path) -> None:
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    _write_manifest(manifests_dir, "landingpage")
    fake_service = FakeProjectManifestService()

    monkeypatch.setattr(upload_command, "ProjectManifestService", lambda: fake_service)

    result = runner.invoke(
        app,
        ["upload-project-manifest", "--path", str(manifests_dir), "landingpage"],
    )

    assert result.exit_code == 0
    assert fake_service.uploaded_project_keys == ["landingpage"]
    assert "Created project manifest landingpage (sources: 1, row_id:" in result.output
    assert "Upload summary: created 1, already existing 0, total 1." in result.output


def test_upload_project_manifest_persists_deployment_metadata(
    monkeypatch,
    tmp_path,
) -> None:
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    _write_manifest_with_deployment(manifests_dir, "landingpage")
    fake_service = FakeProjectManifestService()

    monkeypatch.setattr(upload_command, "ProjectManifestService", lambda: fake_service)

    result = runner.invoke(
        app,
        ["upload-project-manifest", "--path", str(manifests_dir), "landingpage"],
    )

    assert result.exit_code == 0
    assert fake_service.created_payloads[0].deployment == {
        "compose_files": ["/opt/landingpage/docker-compose.prod.yml"],
        "current_tag_path": "/var/lib/landingpage/prod/current_tag",
        "expected_image_repositories": {"backend": "landingpage-backend"},
    }


def test_upload_project_manifest_uploads_all_projects(monkeypatch, tmp_path) -> None:
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    _write_manifest(manifests_dir, "zeta")
    _write_manifest(manifests_dir, "alpha")
    fake_service = FakeProjectManifestService()

    monkeypatch.setattr(upload_command, "ProjectManifestService", lambda: fake_service)

    result = runner.invoke(
        app,
        ["upload-project-manifest", "--path", str(manifests_dir), "--all"],
    )

    assert result.exit_code == 0
    assert fake_service.uploaded_project_keys == ["alpha", "zeta"]
    assert "Upload summary: created 2, already existing 0, total 2." in result.output


def test_upload_project_manifest_reports_existing_without_update(
    monkeypatch,
    tmp_path,
) -> None:
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    _write_manifest(manifests_dir, "landingpage")
    fake_service = FakeProjectManifestService()
    fake_service.existing_project_keys.add("landingpage")

    monkeypatch.setattr(upload_command, "ProjectManifestService", lambda: fake_service)

    result = runner.invoke(
        app,
        ["upload-project-manifest", "--path", str(manifests_dir), "landingpage"],
    )

    assert result.exit_code == 0
    assert fake_service.uploaded_project_keys == []
    assert fake_service.updated_project_keys == []
    assert "Project manifest landingpage already exists and was not changed." in result.output
    assert "uv run command update-project-manifest --project landingpage" in result.output
    assert "Upload summary: created 0, already existing 1, total 1." in result.output


def test_update_project_manifest_updates_existing_project(
    monkeypatch,
    tmp_path,
) -> None:
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    _write_manifest(manifests_dir, "landingpage")
    fake_service = FakeProjectManifestService()
    fake_service.existing_project_keys.add("landingpage")

    monkeypatch.setattr(upload_command, "ProjectManifestService", lambda: fake_service)

    result = runner.invoke(
        app,
        [
            "update-project-manifest",
            "--path",
            str(manifests_dir),
            "--project",
            "landingpage",
        ],
    )

    assert result.exit_code == 0
    assert len(fake_service.updated_project_keys) == 1
    assert "Updated project manifest landingpage (sources: 1, row_id:" in result.output


def test_update_project_manifest_persists_deployment_metadata(
    monkeypatch,
    tmp_path,
) -> None:
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    _write_manifest_with_deployment(manifests_dir, "landingpage")
    fake_service = FakeProjectManifestService()
    fake_service.existing_project_keys.add("landingpage")

    monkeypatch.setattr(upload_command, "ProjectManifestService", lambda: fake_service)

    result = runner.invoke(
        app,
        [
            "update-project-manifest",
            "--path",
            str(manifests_dir),
            "--project",
            "landingpage",
        ],
    )

    assert result.exit_code == 0
    assert fake_service.updated_payloads[0].deployment == {
        "compose_files": ["/opt/landingpage/docker-compose.prod.yml"],
        "current_tag_path": "/var/lib/landingpage/prod/current_tag",
        "expected_image_repositories": {"backend": "landingpage-backend"},
    }


def test_update_project_manifest_updates_all_existing_projects(
    monkeypatch,
    tmp_path,
) -> None:
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    _write_manifest(manifests_dir, "zeta")
    _write_manifest(manifests_dir, "alpha")
    fake_service = FakeProjectManifestService()
    fake_service.existing_project_keys.update({"alpha", "zeta"})

    monkeypatch.setattr(upload_command, "ProjectManifestService", lambda: fake_service)

    result = runner.invoke(
        app,
        ["update-project-manifest", "--path", str(manifests_dir), "--all"],
    )

    assert result.exit_code == 0
    assert len(fake_service.updated_project_keys) == 2
    assert "Updated project manifest alpha (sources: 1, row_id:" in result.output
    assert "Updated project manifest zeta (sources: 1, row_id:" in result.output
    assert "Update summary: updated 2, missing 0, total 2." in result.output


def test_update_project_manifest_updates_all_and_reports_missing_projects(
    monkeypatch,
    tmp_path,
) -> None:
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    _write_manifest(manifests_dir, "landingpage")
    _write_manifest(manifests_dir, "shop")
    fake_service = FakeProjectManifestService()
    fake_service.existing_project_keys.add("landingpage")

    monkeypatch.setattr(upload_command, "ProjectManifestService", lambda: fake_service)

    result = runner.invoke(
        app,
        ["update-project-manifest", "--path", str(manifests_dir), "--all"],
    )

    assert result.exit_code == 0
    assert len(fake_service.updated_project_keys) == 1
    assert "Updated project manifest landingpage (sources: 1, row_id:" in result.output
    assert "Project manifest shop does not exist." in result.output
    assert "Update summary: updated 1, missing 1, total 2." in result.output


def test_update_project_manifest_reports_missing_project(
    monkeypatch,
    tmp_path,
) -> None:
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    _write_manifest(manifests_dir, "landingpage")
    fake_service = FakeProjectManifestService()

    monkeypatch.setattr(upload_command, "ProjectManifestService", lambda: fake_service)

    result = runner.invoke(
        app,
        [
            "update-project-manifest",
            "--path",
            str(manifests_dir),
            "--project",
            "landingpage",
        ],
    )

    assert result.exit_code == 0
    assert fake_service.updated_project_keys == []
    assert "Project manifest landingpage does not exist." in result.output
    assert "uv run command upload-project-manifest landingpage" in result.output


def test_update_project_manifest_requires_selection() -> None:
    result = runner.invoke(app, ["update-project-manifest"])

    assert result.exit_code == 2
    with pytest.raises(typer.BadParameter, match="Provide --project PROJECT_NAME or use --all."):
        upload_command.update_project_manifest()


def test_update_project_manifest_rejects_project_and_all() -> None:
    result = runner.invoke(
        app,
        ["update-project-manifest", "--project", "landingpage", "--all"],
    )

    assert result.exit_code == 2
    with pytest.raises(
        typer.BadParameter,
        match="Use either --project or --all, not both.",
    ):
        upload_command.update_project_manifest(
            project_name="landingpage",
            all_projects=True,
        )


def test_update_project_manifest_command_requires_selection() -> None:
    result = runner.invoke(app, ["update-project-manifest"])

    assert result.exit_code == 2
    with pytest.raises(typer.BadParameter, match="Provide --project PROJECT_NAME or use --all."):
        upload_command.update_project_manifest()


def test_update_project_manifest_command_rejects_project_and_all() -> None:
    result = runner.invoke(
        app,
        ["update-project-manifest", "--project", "landingpage", "--all"],
    )

    assert result.exit_code == 2
    with pytest.raises(
        typer.BadParameter,
        match="Use either --project or --all, not both.",
    ):
        upload_command.update_project_manifest(project_name="landingpage", all_projects=True)


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


def test_upload_project_manifest_defaults_to_configured_manifest_path(
    monkeypatch,
    tmp_path,
) -> None:
    manifests_dir = tmp_path / "configured-manifests"
    manifests_dir.mkdir()
    _write_manifest(manifests_dir, "landingpage")
    fake_service = FakeProjectManifestService()

    monkeypatch.setattr(upload_command, "ProjectManifestService", lambda: fake_service)
    monkeypatch.setattr(
        upload_command.settings,
        "PROJECT_MANIFESTS_PATH",
        manifests_dir,
    )

    result = runner.invoke(app, ["upload-project-manifest", "landingpage"])

    assert result.exit_code == 0
    assert fake_service.uploaded_project_keys == ["landingpage"]
