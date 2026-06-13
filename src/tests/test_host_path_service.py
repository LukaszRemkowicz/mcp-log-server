from __future__ import annotations

import os
from pathlib import Path

import pytest

from manifests.models import SourceDefinition
from services.host_path_service import HostPathService, HostPathServiceError


def _file_source(target: Path, inspect_path_prefixes: list[str] | None = None) -> SourceDefinition:
    return SourceDefinition(
        source_key="app_file",
        source_type="file",
        target=target.as_posix(),
        description="Application file logs.",
        parser_type="plain_text",
        normalization_profile="app_logs",
        retention_class="short",
        default_noise_profile="app_noise",
        inspect_path_prefixes=inspect_path_prefixes or [],
    )


def test_host_path_service_stats_default_source_file(tmp_path: Path) -> None:
    log_file = tmp_path / "app.log"
    log_file.write_text("line one\n", encoding="utf-8")

    result = HostPathService().stat_project_path(_file_source(log_file), None)

    assert not isinstance(result, HostPathServiceError)
    assert result.path == log_file.as_posix()
    assert result.name == "app.log"
    assert result.exists is True
    assert result.is_file is True
    assert result.is_dir is False
    assert result.size == len("line one\n")
    assert result.readable is True


def test_host_path_service_lists_default_parent_directory(tmp_path: Path) -> None:
    log_file = tmp_path / "app.log"
    rotated_file = tmp_path / "app.log.1"
    log_file.write_text("line one\n", encoding="utf-8")
    rotated_file.write_text("old line\n", encoding="utf-8")

    result = HostPathService().list_project_directory(_file_source(log_file), None)

    assert not isinstance(result, HostPathServiceError)
    entries, truncated = result
    assert truncated is False
    assert [entry.name for entry in entries] == ["app.log", "app.log.1"]


def test_host_path_service_reads_file_with_byte_limit(tmp_path: Path) -> None:
    log_file = tmp_path / "app.log"
    log_file.write_text("abcdef", encoding="utf-8")

    result = HostPathService().read_project_file(_file_source(log_file), None, max_bytes=3)

    assert not isinstance(result, HostPathServiceError)
    content, truncated = result
    assert content == "abc"
    assert truncated is True


def test_host_path_service_rejects_parent_traversal(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "app.log"
    log_file.parent.mkdir()
    log_file.write_text("line\n", encoding="utf-8")

    result = HostPathService().stat_project_path(_file_source(log_file), "../secret.txt")

    assert result == HostPathServiceError(
        message="Project path inspection may not include parent directory traversal."
    )


def test_host_path_service_rejects_paths_outside_allowlist(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "app.log"
    secret_file = tmp_path / "secret.txt"
    log_file.parent.mkdir()
    log_file.write_text("line\n", encoding="utf-8")
    secret_file.write_text("secret\n", encoding="utf-8")

    result = HostPathService().stat_project_path(_file_source(log_file), secret_file.as_posix())

    assert result == HostPathServiceError(
        message="Requested project path is outside the manifest whitelist for the selected source."
    )


@pytest.mark.skipif(
    not hasattr(os, "symlink"),
    reason="symlink support is required for this safety check",
)
def test_host_path_service_rejects_symlink_escape(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "app.log"
    secret_file = tmp_path / "secret.txt"
    symlink_path = tmp_path / "logs" / "escaped"
    log_file.parent.mkdir()
    log_file.write_text("line\n", encoding="utf-8")
    secret_file.write_text("secret\n", encoding="utf-8")
    symlink_path.symlink_to(secret_file)

    result = HostPathService().stat_project_path(_file_source(log_file), symlink_path.as_posix())

    assert result == HostPathServiceError(
        message="Requested project path symlink resolves outside the manifest whitelist."
    )
