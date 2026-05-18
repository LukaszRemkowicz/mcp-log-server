from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from manifests.models import SourceDefinition
from services.docker_service import (
    ContainerDetail,
    ContainerDetailMount,
    ContainerDetailNetwork,
    ContainerDetailPort,
    ContainerHealth,
    ContainerPathStat,
    ContainerRestartPolicy,
    DockerService,
    DockerServiceError,
)
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


def test_run_container_command_reuses_shared_container_lookup(
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
    get_container = mocker.patch.object(
        DockerService,
        "_get_container",
        return_value=fake_docker_client,
    )

    result = DockerService().stat_container_path("backend-container", "/app/manage.py")

    get_container.assert_called_once_with("backend-container")
    assert isinstance(result, ContainerPathStat)
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


def test_inspect_container_health_returns_structured_docker_state(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
) -> None:
    fake_docker_client.attrs = {
        "Id": "abc123def456",
        "Name": "/backend-container",
        "Config": {"Image": "portfolio/backend:2026-05-16"},
        "RestartCount": 2,
        "State": {
            "Status": "running",
            "Running": True,
            "Restarting": False,
            "Paused": False,
            "Dead": False,
            "ExitCode": 0,
            "Error": "",
            "StartedAt": "2026-05-16T10:00:00.000000000Z",
            "FinishedAt": "2026-05-16T09:30:00.000000000Z",
            "Health": {
                "Status": "healthy",
                "FailingStreak": 0,
            },
        },
    }

    mocker.patch(
        "services.docker_service.docker.from_env",
        return_value=fake_docker_client,
    )

    result = DockerService().inspect_container_health("backend-container")

    assert result == ContainerHealth(
        container_id="abc123def456",
        container_name="backend-container",
        image="portfolio/backend:2026-05-16",
        docker_status="running",
        health_status="healthy",
        running=True,
        restarting=False,
        paused=False,
        dead=False,
        exit_code=0,
        error="",
        restart_count=2,
        started_at="2026-05-16T10:00:00.000000000Z",
        finished_at=None,
    )


def test_inspect_container_health_preserves_finished_at_for_stopped_container(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
) -> None:
    fake_docker_client.attrs = {
        "Id": "abc123def456",
        "Name": "/backend-container",
        "Config": {"Image": "portfolio/backend:2026-05-16"},
        "RestartCount": 2,
        "State": {
            "Status": "exited",
            "Running": False,
            "Restarting": False,
            "Paused": False,
            "Dead": False,
            "ExitCode": 1,
            "Error": "",
            "StartedAt": "2026-05-16T10:00:00.000000000Z",
            "FinishedAt": "2026-05-16T10:05:00.000000000Z",
        },
    }

    mocker.patch(
        "services.docker_service.docker.from_env",
        return_value=fake_docker_client,
    )

    result = DockerService().inspect_container_health("backend-container")

    assert isinstance(result, ContainerHealth)
    assert result.running is False
    assert result.finished_at == "2026-05-16T10:05:00.000000000Z"


def test_inspect_container_detail_returns_curated_docker_metadata(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
) -> None:
    fake_docker_client.attrs = {
        "Id": "abc123def456",
        "Name": "/backend-container",
        "Created": "2026-05-16T09:55:00.000000000Z",
        "Config": {
            "Image": "portfolio/backend:2026-05-16",
            "Env": ["SECRET_KEY=hidden", "DJANGO_SETTINGS_MODULE=app.settings"],
            "Entrypoint": ["/entrypoint.sh"],
            "Cmd": ["gunicorn", "app.wsgi:application"],
            "WorkingDir": "/app",
            "User": "app",
            "Labels": {
                "com.docker.compose.project": "portfolio",
                "com.docker.compose.service": "backend",
                "com.docker.compose.container-number": "1",
                "secret.label": "hidden",
            },
        },
        "HostConfig": {
            "RestartPolicy": {
                "Name": "unless-stopped",
                "MaximumRetryCount": 3,
            }
        },
        "RestartCount": 2,
        "State": {
            "Status": "running",
            "Running": True,
            "Restarting": False,
            "Paused": False,
            "Dead": False,
            "ExitCode": 0,
            "Error": "",
            "StartedAt": "2026-05-16T10:00:00.000000000Z",
            "FinishedAt": "2026-05-16T09:30:00.000000000Z",
            "Health": {
                "Status": "healthy",
                "FailingStreak": 0,
                "Log": [
                    {
                        "Start": "2026-05-16T10:01:00.000000000Z",
                        "End": "2026-05-16T10:01:01.000000000Z",
                        "ExitCode": 0,
                        "Output": "ok\n",
                    }
                ],
            },
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": "/host/app",
                "Destination": "/app",
                "Mode": "rw",
                "RW": True,
            }
        ],
        "NetworkSettings": {
            "Ports": {
                "8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18080"}],
                "9000/tcp": None,
            },
            "Networks": {
                "web": {
                    "IPAddress": "172.20.0.10",
                    "Aliases": ["backend", "api"],
                }
            },
        },
    }

    mocker.patch(
        "services.docker_service.docker.from_env",
        return_value=fake_docker_client,
    )

    result = DockerService().inspect_container_detail("backend-container")

    assert result == ContainerDetail(
        health=ContainerHealth(
            container_id="abc123def456",
            container_name="backend-container",
            image="portfolio/backend:2026-05-16",
            docker_status="running",
            health_status="healthy",
            running=True,
            restarting=False,
            paused=False,
            dead=False,
            exit_code=0,
            error="",
            restart_count=2,
            started_at="2026-05-16T10:00:00.000000000Z",
            finished_at=None,
        ),
        created_at="2026-05-16T09:55:00.000000000Z",
        env_var_names=["SECRET_KEY", "DJANGO_SETTINGS_MODULE"],
        label_keys=[
            "com.docker.compose.project",
            "com.docker.compose.service",
            "com.docker.compose.container-number",
            "secret.label",
        ],
        compose_labels={
            "com.docker.compose.container-number": "1",
            "com.docker.compose.project": "portfolio",
            "com.docker.compose.service": "backend",
        },
        restart_policy=ContainerRestartPolicy(
            name="unless-stopped",
            maximum_retry_count=3,
        ),
        command=["gunicorn", "app.wsgi:application"],
        entrypoint=["/entrypoint.sh"],
        working_dir="/app",
        user="app",
        ports=[
            ContainerDetailPort(
                private_port="8000/tcp",
                host_ip="127.0.0.1",
                host_port="18080",
            ),
            ContainerDetailPort(
                private_port="9000/tcp",
                host_ip=None,
                host_port=None,
            ),
        ],
        mounts=[
            ContainerDetailMount(
                type="bind",
                destination="/app",
                mode="rw",
                rw=True,
            )
        ],
        networks=[
            ContainerDetailNetwork(
                name="web",
                ip_address="172.20.0.10",
                aliases=["backend", "api"],
            )
        ],
        health_log=[
            {
                "start": "2026-05-16T10:01:00.000000000Z",
                "end": "2026-05-16T10:01:01.000000000Z",
                "exit_code": 0,
                "output": "ok\n",
            }
        ],
    )
