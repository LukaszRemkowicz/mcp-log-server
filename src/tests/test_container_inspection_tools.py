from __future__ import annotations

from unittest.mock import patch

from fastmcp.server.auth import AccessToken

from tests.conftest import FileSourceManifestFactory, override_settings
from tools.container_inspection import (
    list_container_directory,
    read_container_file,
    stat_container_path,
)
from utils.container_inspection_commands import ContainerPathStat


def test_read_container_file_reads_whitelisted_project_file(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    manifest_path = file_source_manifest_factory.create(
        target="app-container",
        source_key="backend",
        source_type="docker",
        inspect_path_prefixes=["/app/"],
    )
    token = AccessToken(
        token="codex-dev-token",
        client_id="codex-agent",
        scopes=["container.files.read"],
        claims={"sub": "codex-agent", "project_key": "landingpage"},
    )

    with (
        override_settings(MANIFEST_PATH=manifest_path),
        patch(
            "tools.container_inspection.run_stat_container_path",
            return_value=ContainerPathStat(
                path="/app/VERSION",
                is_dir=False,
                size=11,
                mode=0o100644,
                modified_at="2026-04-26T10:00:00+00:00",
            ),
        ),
        patch(
            "tools.container_inspection.run_read_container_file",
            return_value=("2026.04.26\n", False),
        ),
    ):
        result = read_container_file(
            project_name="landingpage",
            source_key="backend",
            path="/app/VERSION",
            access_token=token,
        )

    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "read_container_file"
    assert payload["source_key"] == "backend"
    assert payload["container_name"] == "app-container"
    assert payload["path"] == "/app/VERSION"
    assert payload["content"] == "2026.04.26\n"
    assert payload["truncated"] is False
    assert payload["file"]["name"] == "VERSION"


def test_read_container_file_rejects_non_whitelisted_path(
    file_source_manifest_factory: FileSourceManifestFactory,
    tmp_path,
) -> None:
    manifest_path = file_source_manifest_factory.create(
        target="app-container",
        source_key="backend",
        source_type="docker",
        inspect_path_prefixes=["/app/"],
    )
    token = AccessToken(
        token="codex-dev-token",
        client_id="codex-agent",
        scopes=["container.files.read"],
        claims={"sub": "codex-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path):
        result = read_container_file(
            project_name="landingpage",
            source_key="backend",
            path="/etc/passwd",
            access_token=token,
        )

    payload = result.structured_content
    assert payload is not None

    assert payload["error_code"] == "container_path_not_allowed"
    assert payload["action"] == "read_container_file"
    assert payload["content"] == ""
    assert payload["file"] is None
    assert payload["max_bytes"] == 200000
    assert payload["path"] == "/etc/passwd"


def test_read_container_file_rejects_parent_directory_traversal(
    file_source_manifest_factory: FileSourceManifestFactory,
    tmp_path,
) -> None:
    manifest_path = file_source_manifest_factory.create(
        target="app-container",
        source_key="backend",
        source_type="docker",
        inspect_path_prefixes=["/app/"],
    )
    token = AccessToken(
        token="codex-dev-token",
        client_id="codex-agent",
        scopes=["container.files.read"],
        claims={"sub": "codex-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path):
        result = read_container_file(
            project_name="landingpage",
            source_key="backend",
            path="/app/../etc/passwd",
            access_token=token,
        )

    payload = result.structured_content
    assert payload is not None

    assert payload["error_code"] == "container_path_parent_traversal"
    assert payload["action"] == "read_container_file"
    assert payload["content"] == ""
    assert payload["file"] is None
    assert payload["path"] == "/app/../etc/passwd"


def test_list_container_directory_lists_immediate_entries(
    file_source_manifest_factory: FileSourceManifestFactory,
    tmp_path,
) -> None:
    manifest_path = file_source_manifest_factory.create(
        target="frontend-container",
        source_key="frontend",
        source_type="docker",
        inspect_path_prefixes=["/app/"],
    )
    token = AccessToken(
        token="codex-dev-token",
        client_id="codex-agent",
        scopes=["container.files.read"],
        claims={"sub": "codex-agent", "project_key": "landingpage"},
    )

    with (
        override_settings(MANIFEST_PATH=manifest_path),
        patch(
            "tools.container_inspection.run_stat_container_path",
            return_value=ContainerPathStat(
                path="/app",
                is_dir=True,
                size=0,
                mode=0o040755,
                modified_at="2026-04-26T10:00:00+00:00",
            ),
        ),
        patch(
            "tools.container_inspection.run_list_container_directory",
            return_value=(
                [
                    ContainerPathStat(
                        path="/app/src",
                        is_dir=True,
                        size=0,
                        mode=0o040755,
                        modified_at="2026-04-26T10:00:00+00:00",
                    ),
                    ContainerPathStat(
                        path="/app/VERSION",
                        is_dir=False,
                        size=2,
                        mode=0o100644,
                        modified_at="2026-04-26T10:00:00+00:00",
                    ),
                ],
                False,
            ),
        ),
    ):
        result = list_container_directory(
            project_name="landingpage",
            source_key="frontend",
            path="/app",
            access_token=token,
        )

    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "list_container_directory"
    assert [entry["name"] for entry in payload["entries"]] == ["src", "VERSION"]
    assert payload["entries"][0]["is_dir"] is True
    assert payload["entries"][1]["is_dir"] is False


def test_stat_container_path_returns_metadata(
    file_source_manifest_factory: FileSourceManifestFactory,
    tmp_path,
) -> None:
    manifest_path = file_source_manifest_factory.create(
        target="nginx-container",
        source_key="nginx",
        source_type="docker",
        inspect_path_prefixes=["/etc/nginx/"],
    )
    token = AccessToken(
        token="codex-dev-token",
        client_id="codex-agent",
        scopes=["container.files.read"],
        claims={"sub": "codex-agent", "project_key": "landingpage"},
    )

    with (
        override_settings(MANIFEST_PATH=manifest_path),
        patch(
            "tools.container_inspection.run_stat_container_path",
            return_value=ContainerPathStat(
                path="/etc/nginx/nginx.conf",
                is_dir=False,
                size=23,
                mode=0o100644,
                modified_at="2026-04-26T10:00:00+00:00",
            ),
        ),
    ):
        result = stat_container_path(
            project_name="landingpage",
            source_key="nginx",
            path="/etc/nginx/nginx.conf",
            access_token=token,
        )

    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "stat_container_path"
    assert payload["stat"]["path"] == "/etc/nginx/nginx.conf"
    assert payload["stat"]["is_dir"] is False
