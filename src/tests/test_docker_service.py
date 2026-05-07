from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from manifests.models import SourceDefinition
from services.docker_service import ContainerPathStat, DockerService, DockerServiceError
from tests.conftest import FakeDockerClient


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (" /app//settings.py ", "/app/settings.py"),
        ("/app", "/app"),
    ],
)
def test_normalize_container_path_returns_safe_absolute_path(
    path: str,
    expected: str,
) -> None:
    assert DockerService().normalize_container_path(path) == expected


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("app/settings.py", "absolute path"),
        ("/app/../etc/passwd", "parent directory traversal"),
    ],
)
def test_normalize_container_path_rejects_unsafe_paths(
    path: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        DockerService().normalize_container_path(path)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/app", True),
        ("/app/settings.py", True),
        ("/application/settings.py", False),
    ],
)
def test_container_path_is_allowed_uses_manifest_inspection_prefixes(
    path: str,
    expected: bool,
) -> None:
    definition = SourceDefinition(
        source_key="backend",
        source_type="docker",
        target="backend-container",
        description="Backend container",
        parser_type="json",
        normalization_profile="application",
        retention_class="short",
        inspect_path_prefixes=["/app/"],
    )

    assert DockerService().container_path_is_allowed(definition, path) is expected


def test_container_path_is_allowed_rejects_parent_traversal() -> None:
    definition = SourceDefinition(
        source_key="backend",
        source_type="docker",
        target="backend-container",
        description="Backend container",
        parser_type="json",
        normalization_profile="application",
        retention_class="short",
        inspect_path_prefixes=["/app/"],
    )

    with pytest.raises(ValueError, match="parent directory traversal"):
        DockerService().container_path_is_allowed(definition, "/app/../etc/passwd")


@pytest.mark.parametrize("path", [None, "", "   "])
def test_resolve_container_directory_path_defaults_to_first_inspection_prefix(
    path: str | None,
) -> None:
    definition = SourceDefinition(
        source_key="backend",
        source_type="docker",
        target="backend-container",
        description="Backend container",
        parser_type="json",
        normalization_profile="application",
        retention_class="short",
        inspect_path_prefixes=["/app/", "/var/log/app/"],
    )

    assert DockerService().resolve_container_directory_path(definition, path) == "/app"


def test_resolve_container_directory_path_uses_explicit_path() -> None:
    definition = SourceDefinition(
        source_key="backend",
        source_type="docker",
        target="backend-container",
        description="Backend container",
        parser_type="json",
        normalization_profile="application",
        retention_class="short",
        inspect_path_prefixes=["/app/"],
    )

    assert DockerService().resolve_container_directory_path(definition, "/app/src/") == "/app/src"


def test_stat_container_path_runs_only_the_approved_find_and_stat_command(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
) -> None:
    expected_command = [
        "find",
        "/app/manage.py",
        "-maxdepth",
        "0",
        "(",
        "-type",
        "f",
        "-o",
        "-type",
        "d",
        ")",
        "-exec",
        "stat",
        "-c",
        "%F\t%s\t%a\t%Y\t%n",
        "{}",
        ";",
    ]
    fake_docker_client.outputs_by_command = {
        tuple(expected_command): "regular file\t661\t755\t1775110909\t/app/manage.py\n",
    }

    mocker.patch(
        "services.docker_service.docker.from_env",
        return_value=fake_docker_client,
    )
    result = DockerService().stat_container_path("backend-container", "/app/manage.py")

    assert result == ContainerPathStat(
        path="/app/manage.py",
        is_dir=False,
        size=661,
        mode=0o755,
        modified_at="2026-04-02T06:21:49+00:00",
    )
    assert fake_docker_client.commands == [expected_command]


def test_stat_container_path_returns_error_when_command_has_no_stat_line(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
) -> None:
    expected_command = [
        "find",
        "/app/missing.py",
        "-maxdepth",
        "0",
        "(",
        "-type",
        "f",
        "-o",
        "-type",
        "d",
        ")",
        "-exec",
        "stat",
        "-c",
        "%F\t%s\t%a\t%Y\t%n",
        "{}",
        ";",
    ]
    fake_docker_client.outputs_by_command = {tuple(expected_command): "\n"}

    mocker.patch(
        "services.docker_service.docker.from_env",
        return_value=fake_docker_client,
    )
    result = DockerService().stat_container_path("backend-container", "/app/missing.py")

    assert result == DockerServiceError(message="Requested container path was not found.")
    assert fake_docker_client.commands == [expected_command]


def test_read_container_file_runs_only_approved_stat_then_cat_commands(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
) -> None:
    stat_command = [
        "find",
        "/app/manage.py",
        "-maxdepth",
        "0",
        "(",
        "-type",
        "f",
        "-o",
        "-type",
        "d",
        ")",
        "-exec",
        "stat",
        "-c",
        "%F\t%s\t%a\t%Y\t%n",
        "{}",
        ";",
    ]
    cat_command = [
        "find",
        "/app/manage.py",
        "-maxdepth",
        "0",
        "-type",
        "f",
        "-exec",
        "cat",
        "{}",
        ";",
    ]
    fake_docker_client.outputs_by_command = {
        tuple(stat_command): "regular file\t661\t755\t1775110909\t/app/manage.py\n",
        tuple(cat_command): "#!/usr/bin/env python\n",
    }

    mocker.patch(
        "services.docker_service.docker.from_env",
        return_value=fake_docker_client,
    )
    content, truncated = DockerService().read_container_file(
        "backend-container",
        "/app/manage.py",
    )

    assert content == "#!/usr/bin/env python\n"
    assert truncated is False
    assert fake_docker_client.commands == [stat_command, cat_command]


def test_list_container_directory_runs_only_approved_stat_commands(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
) -> None:
    directory_stat_command = [
        "find",
        "/app/settings",
        "-maxdepth",
        "0",
        "(",
        "-type",
        "f",
        "-o",
        "-type",
        "d",
        ")",
        "-exec",
        "stat",
        "-c",
        "%F\t%s\t%a\t%Y\t%n",
        "{}",
        ";",
    ]
    list_command = [
        "find",
        "/app/settings",
        "-mindepth",
        "1",
        "-maxdepth",
        "1",
        "(",
        "-type",
        "f",
        "-o",
        "-type",
        "d",
        ")",
        "-exec",
        "stat",
        "-c",
        "%F\t%s\t%a\t%Y\t%n",
        "{}",
        ";",
    ]
    fake_docker_client.outputs_by_command = {
        tuple(directory_stat_command): "directory\t224\t755\t1777168800\t/app/settings\n",
        tuple(list_command): (
            "regular file\t1024\t644\t1777168800\t/app/settings/base.py\n"
            "directory\t160\t755\t1777168800\t/app/settings/dev\n"
        ),
    }

    mocker.patch(
        "services.docker_service.docker.from_env",
        return_value=fake_docker_client,
    )
    entries, truncated = DockerService().list_container_directory(
        "backend-container",
        "/app/settings",
    )

    assert truncated is False
    assert entries == [
        ContainerPathStat(
            path="/app/settings/dev",
            is_dir=True,
            size=160,
            mode=0o755,
            modified_at="2026-04-26T02:00:00+00:00",
        ),
        ContainerPathStat(
            path="/app/settings/base.py",
            is_dir=False,
            size=1024,
            mode=0o644,
            modified_at="2026-04-26T02:00:00+00:00",
        ),
    ]
    assert fake_docker_client.commands == [directory_stat_command, list_command]


def test_list_container_directory_returns_single_entry_for_file_path(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
) -> None:
    file_stat_command = [
        "find",
        "/app/VERSION",
        "-maxdepth",
        "0",
        "(",
        "-type",
        "f",
        "-o",
        "-type",
        "d",
        ")",
        "-exec",
        "stat",
        "-c",
        "%F\t%s\t%a\t%Y\t%n",
        "{}",
        ";",
    ]
    fake_docker_client.outputs_by_command = {
        tuple(file_stat_command): "regular file\t12\t644\t1777168800\t/app/VERSION\n",
    }

    mocker.patch(
        "services.docker_service.docker.from_env",
        return_value=fake_docker_client,
    )
    entries, truncated = DockerService().list_container_directory(
        "backend-container",
        "/app/VERSION",
    )

    assert truncated is False
    assert entries == [
        ContainerPathStat(
            path="/app/VERSION",
            is_dir=False,
            size=12,
            mode=0o644,
            modified_at="2026-04-26T02:00:00+00:00",
        )
    ]
    assert fake_docker_client.commands == [file_stat_command]
