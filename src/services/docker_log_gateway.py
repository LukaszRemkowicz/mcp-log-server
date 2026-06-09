"""Docker SDK gateway for deterministic log collection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from docker.errors import APIError, DockerException
from requests import exceptions as requests_exceptions

import docker

if TYPE_CHECKING:
    from docker.client import DockerClient  # type: ignore[import-not-found]

DOCKER_LOG_TIMEOUT_SECONDS = 15
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
COMPOSE_ONEOFF_LABEL = "com.docker.compose.oneoff"


@dataclass(frozen=True, slots=True)
class ResolvedDockerContainer:
    """Container selected for Docker log collection."""

    name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DockerLogGatewayError(Exception):
    """Expected Docker log gateway failure."""

    message: str
    error_code: str = "docker_log_gateway_error"


class DockerLogGatewayProtocol(Protocol):
    """Container resolution and log streaming contract used by collection."""

    def resolve_container_by_name(self, container_name: str) -> ResolvedDockerContainer | None: ...

    def resolve_container_by_project_service(
        self,
        *,
        project_name: str,
        service_name: str,
    ) -> ResolvedDockerContainer | None: ...

    def stream_logs(
        self,
        *,
        container_name: str,
        logs_kwargs: dict[str, int | str | datetime],
    ): ...


class DockerLogGateway:
    """Resolve Docker log containers and stream logs through the Docker SDK."""

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> DockerLogGateway:
        """Create a Docker log gateway from the current Docker environment."""

        try:
            client: DockerClient = docker.from_env(  # type: ignore[attr-defined]
                timeout=DOCKER_LOG_TIMEOUT_SECONDS
            )
        except DockerException as error:
            raise DockerLogGatewayError(
                message="Docker Engine API is not available in the current runtime.",
                error_code="docker_engine_unavailable",
            ) from error
        return cls(client=client)

    def resolve_container_by_name(self, container_name: str) -> ResolvedDockerContainer | None:
        """Return the newest running container that exactly matches a container name."""

        containers = self._list_containers(filters={"name": container_name})
        exact_matches = [
            container
            for container in containers
            if self._container_name(container) == container_name
        ]
        return self._newest_container(exact_matches)

    def resolve_container_by_project_service(
        self,
        *,
        project_name: str,
        service_name: str,
    ) -> ResolvedDockerContainer | None:
        """Return the newest non-one-off Compose service container."""

        containers = self._list_containers(
            filters={
                "label": [
                    f"{COMPOSE_PROJECT_LABEL}={project_name}",
                    f"{COMPOSE_SERVICE_LABEL}={service_name}",
                ],
            }
        )
        service_containers = [
            container for container in containers if not self._is_compose_oneoff(container)
        ]
        return self._newest_container(service_containers)

    def stream_logs(
        self,
        *,
        container_name: str,
        logs_kwargs: dict[str, int | str | datetime],
    ):
        """Stream Docker logs for one resolved container."""

        try:
            container = self.client.containers.get(container_name)
            yield from container.logs(
                follow=False,
                timestamps=True,
                stdout=True,
                stderr=True,
                stream=True,
                **logs_kwargs,
            )
        except APIError as error:
            raise DockerLogGatewayError(
                message=str(error).strip() or "Unknown docker error."
            ) from error
        except requests_exceptions.Timeout as error:
            raise DockerLogGatewayError(
                message=f"Timed out collecting docker logs for {container_name}.",
                error_code="docker_log_timeout",
            ) from error
        except DockerException as error:
            raise DockerLogGatewayError(
                message="Docker Engine API is not available in the current runtime.",
                error_code="docker_engine_unavailable",
            ) from error

    def _list_containers(self, *, filters: dict[str, object]) -> list[Any]:
        """Return running containers for one Docker SDK filter set."""

        try:
            return list(self.client.containers.list(all=False, filters=filters))
        except APIError as error:
            raise DockerLogGatewayError(
                message=str(error).strip() or "Unknown docker error."
            ) from error
        except requests_exceptions.Timeout as error:
            raise DockerLogGatewayError(
                message="Timed out querying docker containers.",
                error_code="docker_query_timeout",
            ) from error
        except DockerException as error:
            raise DockerLogGatewayError(
                message="Docker Engine API is not available in the current runtime.",
                error_code="docker_engine_unavailable",
            ) from error

    @classmethod
    def _newest_container(cls, containers: list[Any]) -> ResolvedDockerContainer | None:
        """Return the newest container from Docker SDK objects."""

        if not containers:
            return None
        newest = max(containers, key=cls._created_at)
        return ResolvedDockerContainer(
            name=cls._container_name(newest),
            created_at=cls._created_at(newest),
        )

    @staticmethod
    def _container_name(container: Any) -> str:
        """Return a stable container name from SDK object metadata."""

        name = getattr(container, "name", "")
        if name:
            return str(name)
        attrs = getattr(container, "attrs", {})
        if isinstance(attrs, dict):
            return str(attrs.get("Name", "")).removeprefix("/")
        return ""

    @staticmethod
    def _created_at(container: Any) -> datetime:
        """Return a normalized creation timestamp from SDK object metadata."""

        attrs = getattr(container, "attrs", {})
        raw_created = ""
        if isinstance(attrs, dict):
            raw_created = str(attrs.get("Created", "")).strip()
        if not raw_created:
            return datetime.min.replace(tzinfo=UTC)
        try:
            created_at = datetime.fromisoformat(raw_created.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
        if created_at.tzinfo is None:
            return created_at.replace(tzinfo=UTC)
        return created_at.astimezone(UTC)

    @staticmethod
    def _is_compose_oneoff(container: Any) -> bool:
        """Return whether a Docker SDK object is a Compose one-off container."""

        attrs = getattr(container, "attrs", {})
        config = attrs.get("Config") if isinstance(attrs, dict) else {}
        labels = config.get("Labels") if isinstance(config, dict) else {}
        if not isinstance(labels, dict):
            return False
        value = labels.get(COMPOSE_ONEOFF_LABEL)
        return str(value).strip().lower() in {"1", "true", "yes"}
