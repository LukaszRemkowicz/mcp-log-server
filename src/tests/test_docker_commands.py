"""Tests for Docker-backed project command execution."""

from __future__ import annotations

from types import SimpleNamespace

from scripts.docker_commands import DockerCommandResult, DockerCommandService
from tests.conftest import FakeDockerExecResult


class FakeComposeContainer:
    """Small fake for a Docker Compose service container."""

    def __init__(self, *, output: str = "done\n", exit_code: int = 0) -> None:
        self.commands: list[list[str]] = []
        self.output = output
        self.exit_code = exit_code

    def exec_run(self, command: list[str], stdout: bool = True, stderr: bool = True):
        assert stdout is True
        assert stderr is True
        self.commands.append(command)
        return FakeDockerExecResult(exit_code=self.exit_code, output=self.output)


def test_run_compose_service_command_uses_compose_labels(mocker) -> None:
    container = FakeComposeContainer(output="uploaded\n", exit_code=0)
    captured_filters: list[dict[str, object]] = []

    def fake_list(*, filters: dict[str, object]):
        captured_filters.append(filters)
        return [container]

    fake_client = SimpleNamespace(containers=SimpleNamespace(list=fake_list))
    mocker.patch("scripts.docker_commands.docker.from_env", return_value=fake_client)

    result = DockerCommandService().run_compose_service_command(
        project_name="mcp-log-server",
        service_name="app",
        command=["uv", "run", "python", "-m", "scripts.main", "upload-project-manifest-internal"],
    )

    assert result == DockerCommandResult(exit_code=0, output="uploaded\n")
    assert captured_filters == [
        {
            "label": [
                "com.docker.compose.project=mcp-log-server",
                "com.docker.compose.service=app",
            ],
            "status": "running",
        }
    ]
    assert container.commands == [
        ["uv", "run", "python", "-m", "scripts.main", "upload-project-manifest-internal"]
    ]


def test_run_compose_service_command_errors_when_service_missing(mocker) -> None:
    fake_client = SimpleNamespace(
        containers=SimpleNamespace(list=lambda *, filters: []),
    )
    mocker.patch("scripts.docker_commands.docker.from_env", return_value=fake_client)

    try:
        DockerCommandService().run_compose_service_command(
            project_name="mcp-log-server",
            service_name="app",
            command=["uv", "run", "commands"],
        )
    except ValueError as error:
        assert str(error) == "Running Compose service mcp-log-server/app was not found."
    else:
        raise AssertionError("Expected missing service to raise ValueError.")
