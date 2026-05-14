"""Tests for local log artifact storage paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from storage import LogFileStorage
from tests.conftest import override_settings


def test_file_storage_builds_workflow_paths(tmp_path: Path) -> None:
    storage = LogFileStorage(root=tmp_path)

    assert storage.workflow_path == tmp_path / "workflow"
    assert (
        storage.workflow_latest_dir("landingpage")
        == tmp_path / "workflow" / "landingpage" / "latest"
    )
    assert (
        storage.workflow_archive_dir("landingpage")
        == tmp_path / "workflow" / "landingpage" / "archive"
    )
    assert storage.workflow_snapshot_paths("landingpage") == (
        tmp_path / "workflow" / "landingpage" / "latest",
        tmp_path / "workflow" / "landingpage" / "archive",
    )


def test_file_storage_builds_session_paths(tmp_path: Path) -> None:
    storage = LogFileStorage(root=tmp_path)

    assert storage.session_path == tmp_path / "sessions"
    assert (
        storage.session_project_dir("session-id", "landingpage")
        == tmp_path / "sessions" / "session-id" / "landingpage"
    )


def test_file_storage_resolves_safe_relative_path(tmp_path: Path) -> None:
    storage = LogFileStorage(root=tmp_path)

    assert storage.path("workflow/landingpage/latest/backend.log") == (
        tmp_path / "workflow" / "landingpage" / "latest" / "backend.log"
    )


@pytest.mark.parametrize("name", ["/tmp/backend.log", "../backend.log", "workflow/../x.log"])
def test_file_storage_rejects_unsafe_relative_path(tmp_path: Path, name: str) -> None:
    storage = LogFileStorage(root=tmp_path)

    with pytest.raises(ValueError, match="relative"):
        storage.path(name)


def test_file_storage_returns_relative_name(tmp_path: Path) -> None:
    storage = LogFileStorage(root=tmp_path)
    path = tmp_path / "workflow" / "landingpage" / "latest" / "backend.log"

    assert storage.relative_name(path) == "workflow/landingpage/latest/backend.log"


def test_file_storage_rejects_relative_name_outside_root(tmp_path: Path) -> None:
    storage = LogFileStorage(root=tmp_path)

    with pytest.raises(ValueError, match="LOGS_DIR"):
        storage.relative_name(tmp_path.parent / "outside.log")


def test_default_storage_uses_runtime_settings(tmp_path: Path) -> None:
    storage = LogFileStorage()

    with override_settings(LOGS_DIR=tmp_path):
        assert storage.location == tmp_path
        assert storage.workflow_latest_dir("landingpage") == (
            tmp_path / "workflow" / "landingpage" / "latest"
        )
