"""Docker-backed command execution helpers for host-side project commands."""

from __future__ import annotations

import io
import tarfile
from dataclasses import dataclass
from pathlib import Path
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
    def _get_compose_service_container(
        client: DockerClient,
        project_name: str,
        service_name: str,
    ):
        """Return the running Compose service container."""

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
        return containers[0]

    @staticmethod
    def _open_docker_client() -> DockerClient:
        """Return a Docker SDK client with the command timeout."""

        return docker.from_env(timeout=DOCKER_COMMAND_TIMEOUT_SECONDS)  # type: ignore[attr-defined]

    def run_compose_service_command(
        self,
        project_name: str,
        service_name: str,
        command: list[str],
    ) -> DockerCommandResult:
        """Run one command inside a Compose service container."""

        try:
            client = self._open_docker_client()
            container = self._get_compose_service_container(client, project_name, service_name)
            result = container.exec_run(command, stdout=True, stderr=True)
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

    def copy_files_to_compose_service(
        self,
        *,
        project_name: str,
        service_name: str,
        files: list[Path],
        target_dir: str,
    ) -> None:
        """Copy files into a directory inside the running Compose service container."""

        try:
            client = self._open_docker_client()
            container = self._get_compose_service_container(client, project_name, service_name)
            mkdir_result = container.exec_run(["mkdir", "-p", target_dir], stdout=True, stderr=True)
            if mkdir_result.exit_code not in (None, 0):
                output = mkdir_result.output.decode("utf-8", errors="replace")
                raise ValueError(output.strip() or f"Unable to create {target_dir}.")

            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w") as tar:
                for file_path in files:
                    tar.add(file_path, arcname=file_path.name)
            archive.seek(0)
            if not container.put_archive(target_dir, archive.read()):
                raise ValueError(f"Unable to copy manifest files to {target_dir}.")
        except APIError as error:
            raise ValueError(str(error).strip() or "Unknown docker error.") from error
        except requests_exceptions.Timeout as error:
            raise ValueError(
                f"Timed out copying files into Compose service {project_name}/{service_name}."
            ) from error
        except DockerException as error:
            raise ValueError(
                "Docker Engine API is not available in the current runtime."
            ) from error
