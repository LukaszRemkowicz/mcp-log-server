from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from manifests.models import SourceDefinition
from services.compose_state_service import ComposeStateService, ComposeStateUnavailable
from services.inspection_tools_service import (
    ContainerDetail,
    ContainerDetailEnvVar,
    ContainerDetailMount,
    ContainerDetailNetwork,
    ContainerDetailPort,
    ContainerHealth,
    ContainerPathStat,
    ContainerRestartPolicy,
    InspectionToolsService,
    InspectionToolsServiceError,
    VpsContainerInventory,
    VpsVolumeInventory,
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
    assert InspectionToolsService().normalize_container_path(path) == expected


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
        InspectionToolsService().normalize_container_path(path)


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

    assert InspectionToolsService().container_path_is_allowed(definition, path) is expected


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
        InspectionToolsService().container_path_is_allowed(definition, "/app/../etc/passwd")


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

    assert InspectionToolsService().resolve_container_directory_path(definition, path) == "/app"


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

    assert (
        InspectionToolsService().resolve_container_directory_path(definition, "/app/src/")
        == "/app/src"
    )


@pytest.mark.skip(reason="Fixed Docker operations now live in socket app.")
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
        "services.inspection_tools_service.docker.from_env",
        return_value=fake_docker_client,
    )
    result = InspectionToolsService().stat_container_path("backend-container", "/app/manage.py")

    assert result == ContainerPathStat(
        path="/app/manage.py",
        is_dir=False,
        size=661,
        mode=0o755,
        modified_at="2026-04-02T06:21:49+00:00",
    )
    assert fake_docker_client.commands == [expected_command]


@pytest.mark.skip(reason="Fixed Docker operations now live in socket app.")
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
        InspectionToolsService,
        "_get_container",
        return_value=fake_docker_client,
    )

    result = InspectionToolsService().stat_container_path("backend-container", "/app/manage.py")

    get_container.assert_called_once_with("backend-container")
    assert isinstance(result, ContainerPathStat)
    assert fake_docker_client.commands == [expected_command]


@pytest.mark.skip(reason="Fixed Docker operations now live in socket app.")
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
        "services.inspection_tools_service.docker.from_env",
        return_value=fake_docker_client,
    )
    result = InspectionToolsService().stat_container_path("backend-container", "/app/missing.py")

    assert result == InspectionToolsServiceError(message="Requested container path was not found.")
    assert fake_docker_client.commands == [expected_command]


@pytest.mark.skip(reason="Fixed Docker operations now live in socket app.")
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
        "services.inspection_tools_service.docker.from_env",
        return_value=fake_docker_client,
    )
    content, truncated = InspectionToolsService().read_container_file(
        "backend-container",
        "/app/manage.py",
    )

    assert content == "#!/usr/bin/env python\n"
    assert truncated is False
    assert fake_docker_client.commands == [stat_command, cat_command]


@pytest.mark.skip(reason="Fixed Docker operations now live in socket app.")
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
        "services.inspection_tools_service.docker.from_env",
        return_value=fake_docker_client,
    )
    entries, truncated = InspectionToolsService().list_container_directory(
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


@pytest.mark.skip(reason="Fixed Docker operations now live in socket app.")
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
        "services.inspection_tools_service.docker.from_env",
        return_value=fake_docker_client,
    )
    entries, truncated = InspectionToolsService().list_container_directory(
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


@pytest.mark.skip(reason="Fixed Docker operations now live in socket app.")
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
        "services.inspection_tools_service.docker.from_env",
        return_value=fake_docker_client,
    )

    result = InspectionToolsService().inspect_container_health("backend-container")

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


@pytest.mark.skip(reason="Fixed Docker operations now live in socket app.")
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
        "services.inspection_tools_service.docker.from_env",
        return_value=fake_docker_client,
    )

    result = InspectionToolsService().inspect_container_health("backend-container")

    assert isinstance(result, ContainerHealth)
    assert result.running is False
    assert result.finished_at == "2026-05-16T10:05:00.000000000Z"


@pytest.mark.skip(reason="Fixed Docker operations now live in socket app.")
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
            "Env": [
                "SECRET_KEY=hidden",
                "DJANGO_SETTINGS_MODULE=app.settings",
                "DATABASE_URL=postgres://user:pass@db/app",
                "NODE_ENV=production",
                "CUSTOM_VALUE=should-not-leak",
            ],
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
        "services.inspection_tools_service.docker.from_env",
        return_value=fake_docker_client,
    )

    result = InspectionToolsService().inspect_container_detail("backend-container")

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
        env_var_names=[
            "SECRET_KEY",
            "DJANGO_SETTINGS_MODULE",
            "DATABASE_URL",
            "NODE_ENV",
            "CUSTOM_VALUE",
        ],
        env_vars=[
            ContainerDetailEnvVar(
                name="SECRET_KEY",
                value=None,
                value_redacted=True,
                secret=True,
            ),
            ContainerDetailEnvVar(
                name="DJANGO_SETTINGS_MODULE",
                value="app.settings",
                value_redacted=False,
                secret=False,
            ),
            ContainerDetailEnvVar(
                name="DATABASE_URL",
                value=None,
                value_redacted=True,
                secret=True,
            ),
            ContainerDetailEnvVar(
                name="NODE_ENV",
                value="production",
                value_redacted=False,
                secret=False,
            ),
            ContainerDetailEnvVar(
                name="CUSTOM_VALUE",
                value=None,
                value_redacted=True,
                secret=False,
            ),
        ],
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


@pytest.mark.skip(reason="Fixed Docker operations now live in socket app.")
def test_inspect_vps_containers_returns_bounded_docker_ps_inventory(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
) -> None:
    class FakeContainer:
        def __init__(self, attrs: dict[str, object]) -> None:
            self.attrs = attrs

    fake_docker_client.listed_containers = [
        FakeContainer(
            {
                "Id": "abc123def4567890",
                "Name": "/backend-container",
                "Created": "2026-05-16T09:55:00.000000000Z",
                "Config": {
                    "Image": "portfolio/backend:2026-05-16",
                    "Cmd": ["gunicorn", "app.wsgi:application", "--timeout", "120"],
                    "Env": ["SECRET_KEY=hidden"],
                    "Labels": {
                        "com.docker.compose.project": "portfolio",
                        "com.docker.compose.service": "backend",
                        "secret.label": "hidden",
                    },
                },
                "HostConfig": {
                    "RestartPolicy": {
                        "Name": "unless-stopped",
                        "MaximumRetryCount": 0,
                    }
                },
                "RestartCount": 8,
                "State": {
                    "Status": "running",
                    "Running": True,
                    "Restarting": False,
                    "Paused": False,
                    "Dead": False,
                    "ExitCode": 0,
                    "Error": "",
                    "StartedAt": "2026-05-16T10:00:00.000000000Z",
                    "Health": {"Status": "unhealthy"},
                },
                "Mounts": [{"Source": "/host/app", "Destination": "/app"}],
                "NetworkSettings": {
                    "Ports": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18080"}]},
                    "Networks": {"web": {"IPAddress": "172.20.0.10"}},
                },
            }
        ),
        FakeContainer(
            {
                "Id": "def456abc1237890",
                "Name": "/worker-container",
                "Created": "2026-05-16T08:00:00.000000000Z",
                "Config": {
                    "Image": "portfolio/worker:2026-05-16",
                    "Cmd": "celery -A app worker",
                    "Labels": {
                        "com.docker.compose.project": "portfolio",
                        "com.docker.compose.service": "worker",
                    },
                },
                "HostConfig": {"RestartPolicy": {"Name": "always"}},
                "RestartCount": 1,
                "State": {
                    "Status": "exited",
                    "Running": False,
                    "Restarting": False,
                    "Paused": False,
                    "Dead": False,
                    "ExitCode": 137,
                    "Error": "",
                    "StartedAt": "2026-05-16T08:05:00.000000000Z",
                    "FinishedAt": "2026-05-16T08:10:00.000000000Z",
                },
                "NetworkSettings": {"Ports": {}, "Networks": {}},
            }
        ),
    ]
    mocker.patch(
        "services.inspection_tools_service.docker.from_env", return_value=fake_docker_client
    )

    result = InspectionToolsService().inspect_vps_containers()

    assert result == [
        VpsContainerInventory(
            container_id="abc123def4567890",
            short_container_id="abc123def456",
            container_name="backend-container",
            image="portfolio/backend:2026-05-16",
            command=["gunicorn", "app.wsgi:application", "--timeout", "120"],
            command_preview="gunicorn app.wsgi:application --timeout 120",
            created_at="2026-05-16T09:55:00.000000000Z",
            docker_status="running",
            state="running",
            health_status="unhealthy",
            running=True,
            restarting=False,
            paused=False,
            dead=False,
            exit_code=0,
            error="",
            restart_count=8,
            started_at="2026-05-16T10:00:00.000000000Z",
            finished_at=None,
            compose_labels={
                "com.docker.compose.project": "portfolio",
                "com.docker.compose.service": "backend",
            },
            restart_policy=ContainerRestartPolicy(name="unless-stopped", maximum_retry_count=0),
            ports=[
                ContainerDetailPort(
                    private_port="8000/tcp",
                    host_ip="127.0.0.1",
                    host_port="18080",
                )
            ],
            network_names=["web"],
            triage_notes=["health_status=unhealthy", "restart_count=8"],
            env_var_names=["SECRET_KEY"],
            mounts=[
                ContainerDetailMount(
                    type=None,
                    destination="/app",
                    mode=None,
                    rw=None,
                    name=None,
                )
            ],
        ),
        VpsContainerInventory(
            container_id="def456abc1237890",
            short_container_id="def456abc123",
            container_name="worker-container",
            image="portfolio/worker:2026-05-16",
            command=["celery -A app worker"],
            command_preview="celery -A app worker",
            created_at="2026-05-16T08:00:00.000000000Z",
            docker_status="exited",
            state="exited",
            health_status=None,
            running=False,
            restarting=False,
            paused=False,
            dead=False,
            exit_code=137,
            error="",
            restart_count=1,
            started_at="2026-05-16T08:05:00.000000000Z",
            finished_at="2026-05-16T08:10:00.000000000Z",
            compose_labels={
                "com.docker.compose.project": "portfolio",
                "com.docker.compose.service": "worker",
            },
            restart_policy=ContainerRestartPolicy(name="always", maximum_retry_count=None),
            ports=[],
            network_names=[],
            triage_notes=["not_running", "exit_code=137"],
        ),
    ]


@pytest.mark.skip(reason="Fixed Docker operations now live in socket app.")
def test_inspect_vps_containers_returns_empty_inventory(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
) -> None:
    fake_docker_client.listed_containers = []
    mocker.patch(
        "services.inspection_tools_service.docker.from_env", return_value=fake_docker_client
    )

    result = InspectionToolsService().inspect_vps_containers()

    assert result == []


def test_compose_state_service_compares_expected_and_running_state() -> None:
    """Verify Compose service identity is inferred from target container labels."""

    sources = [
        SourceDefinition(
            source_key="backend",
            source_type="docker",
            target="backend-container",
            description="Backend container.",
            parser_type="plain_text",
            normalization_profile="app",
            retention_class="short",
        ),
        SourceDefinition(
            source_key="worker",
            source_type="docker",
            target="worker-container",
            description="Worker container.",
            parser_type="plain_text",
            normalization_profile="app",
            retention_class="short",
        ),
    ]
    running = [
        VpsContainerInventory(
            container_id="abc123def4567890",
            short_container_id="abc123def456",
            container_name="backend-container",
            image="portfolio/backend:2026-05-17",
            command=[],
            command_preview="",
            created_at=None,
            docker_status="running",
            state="running",
            health_status="healthy",
            running=True,
            restarting=False,
            paused=False,
            dead=False,
            exit_code=0,
            error="",
            restart_count=0,
            started_at=None,
            finished_at=None,
            compose_labels={
                "com.docker.compose.project": "portfolio",
                "com.docker.compose.service": "backend",
            },
            restart_policy=ContainerRestartPolicy(name="unless-stopped", maximum_retry_count=0),
            ports=[
                ContainerDetailPort(
                    private_port="8000/tcp",
                    host_ip="127.0.0.1",
                    host_port="18080",
                )
            ],
            network_names=[],
            triage_notes=[],
            env_var_names=["DJANGO_SETTINGS_MODULE", "SECRET_KEY"],
            mounts=[
                ContainerDetailMount(
                    type="volume",
                    destination="/app",
                    mode="rw",
                    rw=True,
                    name="portfolio_static",
                )
            ],
        ),
        VpsContainerInventory(
            container_id="def456abc1237890",
            short_container_id="def456abc123",
            container_name="worker-container",
            image="portfolio/worker:2026-05-17",
            command=[],
            command_preview="",
            created_at=None,
            docker_status="exited",
            state="exited",
            health_status=None,
            running=False,
            restarting=False,
            paused=False,
            dead=False,
            exit_code=0,
            error="",
            restart_count=0,
            started_at=None,
            finished_at=None,
            compose_labels={
                "com.docker.compose.project": "portfolio",
                "com.docker.compose.service": "worker",
            },
            restart_policy=ContainerRestartPolicy(name=None, maximum_retry_count=None),
            ports=[],
            network_names=[],
            triage_notes=[],
        ),
        VpsContainerInventory(
            container_id="ghi789abc1234560",
            short_container_id="ghi789abc123",
            container_name="extra-container",
            image="portfolio/extra:2026-05-16",
            command=[],
            command_preview="",
            created_at=None,
            docker_status="running",
            state="running",
            health_status=None,
            running=True,
            restarting=False,
            paused=False,
            dead=False,
            exit_code=0,
            error="",
            restart_count=0,
            started_at=None,
            finished_at=None,
            compose_labels={
                "com.docker.compose.project": "portfolio",
                "com.docker.compose.service": "extra",
            },
            restart_policy=ContainerRestartPolicy(name=None, maximum_retry_count=None),
            ports=[],
            network_names=[],
            triage_notes=[],
        ),
    ]

    result = ComposeStateService().compare(
        project_name="landingpage",
        sources=sources,
        running_containers=running,
    )

    assert not isinstance(result, ComposeStateUnavailable)
    assert result.compose_project == "portfolio"
    assert [service.service_name for service in result.expected_services] == [
        "backend",
        "worker",
    ]
    assert result.running_containers[0].mounts[0].source_redacted is True
    assert result.warnings[0].warning_type.__class__.__name__ == "ComposeStateWarningType"
    assert {warning.warning_type for warning in result.warnings} == {
        "expected_service_not_running",
        "unexpected_running_service",
    }


def test_compose_state_service_returns_unavailable_without_expected_state() -> None:
    """Verify the compose comparison needs a target container with Compose labels."""

    source = SourceDefinition(
        source_key="backend",
        source_type="docker",
        target="backend-container",
        description="Backend container.",
        parser_type="plain_text",
        normalization_profile="app",
        retention_class="short",
    )

    result = ComposeStateService().compare(
        project_name="landingpage",
        sources=[source],
        running_containers=[],
    )

    assert isinstance(result, ComposeStateUnavailable)
    assert "matched a Compose-labelled container" in result.message


@pytest.mark.skip(reason="Fixed Docker operations now live in socket app.")
def test_inspect_vps_volumes_returns_redacted_inventory(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
) -> None:
    class FakeVolume:
        def __init__(self, attrs: dict[str, object]) -> None:
            self.attrs = attrs

    fake_docker_client.listed_volumes = [
        FakeVolume(
            {
                "Name": "dockerpage_db_data",
                "Driver": "local",
                "Mountpoint": "/var/lib/docker/volumes/dockerpage_db_data/_data",
                "CreatedAt": "2026-05-16T09:55:00Z",
                "Scope": "local",
                "Labels": {
                    "com.docker.compose.project": "dockerpage",
                    "com.docker.compose.volume": "db_data",
                    "secret.label": "hidden",
                },
                "Options": {"type": "none"},
                "UsageData": {"RefCount": 2, "Size": 4096},
            }
        ),
        FakeVolume(
            {
                "Name": "other_db_data",
                "Driver": "local",
                "Mountpoint": "/var/lib/docker/volumes/other_db_data/_data",
                "Labels": {"com.docker.compose.project": "other"},
            }
        ),
        FakeVolume(
            {
                "Name": "unlabeled_cache",
                "Driver": "local",
                "Mountpoint": "/var/lib/docker/volumes/unlabeled_cache/_data",
                "Labels": {},
            }
        ),
    ]
    mocker.patch(
        "services.inspection_tools_service.docker.from_env", return_value=fake_docker_client
    )

    result = InspectionToolsService().inspect_vps_volumes()

    assert result == [
        VpsVolumeInventory(
            volume_name="dockerpage_db_data",
            driver="local",
            scope="local",
            created_at="2026-05-16T09:55:00Z",
            compose_labels={
                "com.docker.compose.project": "dockerpage",
                "com.docker.compose.volume": "db_data",
            },
            option_keys=["type"],
            mountpoint_available=True,
            mountpoint_redacted=True,
            usage_ref_count=2,
            usage_size_bytes=4096,
        ),
        VpsVolumeInventory(
            volume_name="other_db_data",
            driver="local",
            scope=None,
            created_at=None,
            compose_labels={"com.docker.compose.project": "other"},
            option_keys=[],
            mountpoint_available=True,
            mountpoint_redacted=True,
            usage_ref_count=None,
            usage_size_bytes=None,
        ),
        VpsVolumeInventory(
            volume_name="unlabeled_cache",
            driver="local",
            scope=None,
            created_at=None,
            compose_labels={},
            option_keys=[],
            mountpoint_available=True,
            mountpoint_redacted=True,
            usage_ref_count=None,
            usage_size_bytes=None,
        ),
    ]


@pytest.mark.skip(reason="Fixed Docker operations now live in socket app.")
def test_inspect_vps_volumes_passes_dangling_filter_to_docker(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
) -> None:
    fake_docker_client.listed_volumes = []
    mocker.patch(
        "services.inspection_tools_service.docker.from_env", return_value=fake_docker_client
    )

    result = InspectionToolsService().inspect_vps_volumes(dangling_only=True)

    assert result == []
    assert fake_docker_client.captured_volume_filters == {"dangling": True}


@pytest.mark.skip(reason="Fixed Docker operations now live in socket app.")
def test_inspect_vps_volumes_filters_by_anonymous_name_and_prefix(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
) -> None:
    class FakeVolume:
        def __init__(self, attrs: dict[str, object]) -> None:
            self.attrs = attrs

    matching_hash = "a" * 64
    other_hash = "b" * 64
    fake_docker_client.listed_volumes = [
        FakeVolume(
            {
                "Name": matching_hash,
                "Driver": "local",
                "Labels": {"com.docker.compose.project": "dockerpage"},
            }
        ),
        FakeVolume(
            {
                "Name": other_hash,
                "Driver": "local",
                "Labels": {"com.docker.compose.project": "dockerpage"},
            }
        ),
        FakeVolume(
            {
                "Name": "dockerpage_db_data",
                "Driver": "local",
                "Labels": {"com.docker.compose.project": "dockerpage"},
            }
        ),
        FakeVolume(
            {
                "Name": matching_hash.replace("a", "c"),
                "Driver": "local",
                "Labels": {"com.docker.compose.project": "other"},
            }
        ),
    ]
    mocker.patch(
        "services.inspection_tools_service.docker.from_env", return_value=fake_docker_client
    )

    result = InspectionToolsService().inspect_vps_volumes(anonymous_only=True, name_prefix="aa")

    assert isinstance(result, list)
    assert [volume.volume_name for volume in result] == [matching_hash]


@pytest.mark.skip(reason="Fixed Docker operations now live in socket app.")
def test_inspect_vps_volumes_returns_empty_inventory(
    fake_docker_client: FakeDockerClient,
    mocker: MockerFixture,
) -> None:
    fake_docker_client.listed_volumes = []
    mocker.patch(
        "services.inspection_tools_service.docker.from_env", return_value=fake_docker_client
    )

    result = InspectionToolsService().inspect_vps_volumes()

    assert result == []
