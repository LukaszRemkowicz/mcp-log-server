from __future__ import annotations

from pathlib import Path

from pytest_mock import MockerFixture

from services.docker_service import ContainerPathStat, DockerServiceError
from tests.conftest import CustomAccessToken, override_settings
from tools.container_inspection import list_container_directory, read_container_file


def test_read_container_file_reads_whitelisted_project_file(
    container_manifests_dir: Path,
    custom_access_token: CustomAccessToken,
    mocker: MockerFixture,
) -> None:
    access_token = custom_access_token(
        "codex-agent",
        ["container.files.read"],
        "codex-agent",
        {"allowed_projects": ["landingpage"]},
    )
    mocker.patch(
        "tools.container_inspection.docker_service.stat_container_path",
        return_value=ContainerPathStat(
            path="/app/VERSION",
            is_dir=False,
            size=11,
            mode=0o100644,
            modified_at="2026-04-26T10:00:00+00:00",
        ),
    )
    mocker.patch(
        "tools.container_inspection.docker_service.read_container_file",
        return_value=("2026.04.26\n", False),
    )

    with override_settings(MANIFEST_PATH=container_manifests_dir):
        result = read_container_file(
            project_name="landingpage",
            source_key="backend",
            path="/app/VERSION",
            access_token=access_token,
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
    container_manifests_dir: Path,
    custom_access_token: CustomAccessToken,
) -> None:
    access_token = custom_access_token(
        "codex-agent",
        ["container.files.read"],
        "codex-agent",
        {"allowed_projects": ["landingpage"]},
    )
    with override_settings(MANIFEST_PATH=container_manifests_dir):
        result = read_container_file(
            project_name="landingpage",
            source_key="backend",
            path="/etc/passwd",
            access_token=access_token,
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
    container_manifests_dir: Path,
    custom_access_token: CustomAccessToken,
) -> None:
    access_token = custom_access_token(
        "codex-agent",
        ["container.files.read"],
        "codex-agent",
        {"allowed_projects": ["landingpage"]},
    )
    with override_settings(MANIFEST_PATH=container_manifests_dir):
        result = read_container_file(
            project_name="landingpage",
            source_key="backend",
            path="/app/../etc/passwd",
            access_token=access_token,
        )

    payload = result.structured_content
    assert payload is not None

    assert payload["error_code"] == "container_path_parent_traversal"
    assert payload["action"] == "read_container_file"
    assert payload["content"] == ""
    assert payload["file"] is None
    assert payload["path"] == "/app/../etc/passwd"


def test_list_container_directory_lists_immediate_entries(
    container_manifests_dir: Path,
    custom_access_token: CustomAccessToken,
    mocker: MockerFixture,
) -> None:
    access_token = custom_access_token(
        "codex-agent",
        ["container.files.read"],
        "codex-agent",
        {"allowed_projects": ["landingpage"]},
    )
    mocker.patch(
        "tools.container_inspection.docker_service.list_container_directory",
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
    )

    with override_settings(MANIFEST_PATH=container_manifests_dir):
        result = list_container_directory(
            project_name="landingpage",
            source_key="frontend",
            path="/app",
            access_token=access_token,
        )

    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "list_container_directory"
    assert [entry["name"] for entry in payload["entries"]] == ["src", "VERSION"]
    assert payload["entries"][0]["is_dir"] is True
    assert payload["entries"][1]["is_dir"] is False


def test_list_container_directory_defaults_to_manifest_inspection_root(
    container_manifests_dir: Path,
    custom_access_token: CustomAccessToken,
    mocker: MockerFixture,
) -> None:
    access_token = custom_access_token(
        "codex-agent",
        ["container.files.read"],
        "codex-agent",
        {"allowed_projects": ["landingpage"]},
    )
    list_directory = mocker.patch(
        "tools.container_inspection.docker_service.list_container_directory",
        return_value=([], False),
    )

    with override_settings(MANIFEST_PATH=container_manifests_dir):
        result = list_container_directory(
            project_name="landingpage",
            source_key="frontend",
            access_token=access_token,
        )

    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "list_container_directory"
    assert payload["path"] == "/app"
    list_directory.assert_called_once_with("frontend-container", "/app")


def test_list_container_directory_returns_single_file_entry(
    container_manifests_dir: Path,
    custom_access_token: CustomAccessToken,
    mocker: MockerFixture,
) -> None:
    access_token = custom_access_token(
        "codex-agent",
        ["container.files.read"],
        "codex-agent",
        {"allowed_projects": ["landingpage"]},
    )
    list_directory = mocker.patch(
        "tools.container_inspection.docker_service.list_container_directory",
        return_value=(
            [
                ContainerPathStat(
                    path="/etc/nginx/nginx.conf",
                    is_dir=False,
                    size=23,
                    mode=0o100644,
                    modified_at="2026-04-26T10:00:00+00:00",
                )
            ],
            False,
        ),
    )

    with override_settings(MANIFEST_PATH=container_manifests_dir):
        result = list_container_directory(
            project_name="landingpage",
            source_key="nginx",
            path="/etc/nginx/nginx.conf",
            access_token=access_token,
        )

    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "list_container_directory"
    assert payload["path"] == "/etc/nginx/nginx.conf"
    assert payload["entries"][0]["path"] == "/etc/nginx/nginx.conf"
    assert payload["entries"][0]["is_dir"] is False
    list_directory.assert_called_once_with("nginx-container", "/etc/nginx/nginx.conf")


def test_list_container_directory_maps_docker_service_error_to_tool_error(
    container_manifests_dir: Path,
    custom_access_token: CustomAccessToken,
    mocker: MockerFixture,
) -> None:
    access_token = custom_access_token(
        "codex-agent",
        ["container.files.read"],
        "codex-agent",
        {"allowed_projects": ["landingpage"]},
    )
    mocker.patch(
        "tools.container_inspection.docker_service.list_container_directory",
        return_value=DockerServiceError(
            message="Requested container path was not found.",
        ),
    )

    with override_settings(MANIFEST_PATH=container_manifests_dir):
        result = list_container_directory(
            project_name="landingpage",
            source_key="nginx",
            path="/etc/nginx/missing.conf",
            access_token=access_token,
        )

    payload = result.structured_content
    assert payload is not None

    assert payload["status"] == "error"
    assert payload["error_code"] == "container_path_not_found"
    assert payload["action"] == "list_container_directory"
    assert payload["entries"] == []


def test_list_container_directory_maps_invalid_path_to_tool_error(
    container_manifests_dir: Path,
    custom_access_token: CustomAccessToken,
) -> None:
    access_token = custom_access_token(
        "codex-agent",
        ["container.files.read"],
        "codex-agent",
        {"allowed_projects": ["landingpage"]},
    )
    with override_settings(MANIFEST_PATH=container_manifests_dir):
        result = list_container_directory(
            project_name="landingpage",
            source_key="nginx",
            path="etc/nginx/nginx.conf",
            access_token=access_token,
        )

    payload = result.structured_content
    assert payload is not None

    assert payload["status"] == "error"
    assert payload["error_code"] == "container_path_not_absolute"
    assert payload["action"] == "list_container_directory"
    assert payload["entries"] == []
