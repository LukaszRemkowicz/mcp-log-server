"""Docker-backed command execution helpers for host-side project commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from docker.errors import APIError, DockerException
from requests import exceptions as requests_exceptions

import docker

if TYPE_CHECKING:
    from docker.client import DockerClient  # type: ignore[import-not-found]

DOCKER_COMMAND_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class DockerCommandResult:
    """Result returned from one command executed inside a Docker container."""

    exit_code: int
    output: str


class DockerCommandService:
    """Execute project commands inside the running Docker Compose app service."""

    @staticmethod
    def run_compose_service_command(
        project_name: str,
        service_name: str,
        command: list[str],
    ) -> DockerCommandResult:
        """Run one command inside a Compose service container."""

        try:
            client: DockerClient = docker.from_env(timeout=DOCKER_COMMAND_TIMEOUT_SECONDS)  # type: ignore[attr-defined]
            containers = client.containers.list(
                filters={
                    "label": [
                        f"com.docker.compose.project={project_name}",
                        f"com.docker.compose.service={service_name}",
                    ],
                    "status": "running",
                }
            )
            if not containers:
                raise ValueError(
                    f"Running Compose service {project_name}/{service_name} was not found."
                )
            result = containers[0].exec_run(command, stdout=True, stderr=True)
        except APIError as error:
            raise ValueError(str(error).strip() or "Unknown docker error.") from error
        except requests_exceptions.Timeout as error:
            raise ValueError(
                f"Timed out running command in Compose service {project_name}/{service_name}."
            ) from error
        except DockerException as error:
            raise ValueError(
                "Docker Engine API is not available in the current runtime."
            ) from error

        exit_code = 0 if result.exit_code is None else int(result.exit_code)
        output = result.output.decode("utf-8", errors="replace")
        return DockerCommandResult(exit_code=exit_code, output=output)
