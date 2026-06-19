"""Build sanitized project runtime inspection results from Compose metadata."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from manifests.models import SourceDefinition
from services.compose_state_service import ComposeStateService, ComposeStateUnavailable
from services.inspection_tools_service import (
    SAFE_ENV_VALUE_NAMES,
    ContainerDetail,
    ContainerDetailEnvVar,
    ContainerDetailMount,
    ContainerDetailPort,
    ContainerRestartPolicy,
    VpsContainerInventory,
)

DATABASE_ENV_KEYS = {
    "host": ("DATABASE_HOST", "DB_HOST", "POSTGRES_HOST"),
    "port": ("DATABASE_PORT", "DB_PORT", "POSTGRES_PORT"),
    "name": ("DATABASE_NAME", "DB_NAME", "POSTGRES_DB"),
    "user": ("DATABASE_USER", "DB_USER", "POSTGRES_USER"),
}


class ProjectRuntimeDatabase(BaseModel):
    """Sanitized database connection shape derived from container env."""

    model_config = ConfigDict(extra="forbid")

    host: str | None
    port: str | None
    name: str | None
    user: str | None
    missing_keys: list[Literal["host", "port", "name", "user"]]


class ProjectRuntimeMount(BaseModel):
    """Container mount metadata without host source paths."""

    model_config = ConfigDict(extra="forbid")

    type: str | None
    destination: str | None
    mode: str | None
    rw: bool | None
    name: str | None
    source_redacted: bool


class ProjectRuntimePort(BaseModel):
    """Published container port metadata."""

    model_config = ConfigDict(extra="forbid")

    private_port: str
    host_ip: str | None
    host_port: str | None


class ProjectRuntimeRestartPolicy(BaseModel):
    """Container restart policy metadata."""

    model_config = ConfigDict(extra="forbid")

    name: str | None
    maximum_retry_count: int | None


class ProjectRuntimeContainer(BaseModel):
    """One sanitized runtime container row for a Compose project."""

    model_config = ConfigDict(extra="forbid")

    container_id: str
    short_container_id: str
    container_name: str
    compose_project: str
    service_name: str | None
    image: str | None
    docker_status: str | None
    state: str | None
    health_status: str | None
    running: bool
    restarting: bool
    paused: bool
    dead: bool
    exit_code: int | None
    error: str | None
    restart_count: int | None
    started_at: str | None
    finished_at: str | None
    created_at: str | None
    restart_policy: ProjectRuntimeRestartPolicy
    compose_labels: dict[str, str]
    ports: list[ProjectRuntimePort]
    network_names: list[str]
    mounts: list[ProjectRuntimeMount]
    env_var_names: list[str]
    selected_env: list[ContainerDetailEnvVar]
    secret_env_names: list[str]
    database: ProjectRuntimeDatabase


class ProjectRuntimeWarning(BaseModel):
    """Non-fatal runtime inspection warning."""

    model_config = ConfigDict(extra="forbid")

    warning_code: str
    container_name: str | None
    message: str


class ProjectRuntimeInspection(BaseModel):
    """Sanitized runtime inspection for one manifest-backed project."""

    model_config = ConfigDict(extra="forbid")

    project_name: str
    compose_project: str | None
    containers: list[ProjectRuntimeContainer]
    warnings: list[ProjectRuntimeWarning]


class ProjectRuntimeService:
    """Aggregate project runtime facts without exposing secret values."""

    def __init__(self, compose_state_service: ComposeStateService | None = None) -> None:
        self.compose_state_service = compose_state_service or ComposeStateService()

    def inspect(
        self,
        *,
        project_name: str,
        sources: list[SourceDefinition],
        running_containers: list[VpsContainerInventory],
        container_details: dict[str, ContainerDetail],
    ) -> ProjectRuntimeInspection | ComposeStateUnavailable:
        """Return sanitized runtime facts for the Compose project behind a manifest."""

        compose_state = self.compose_state_service.compare(
            project_name=project_name,
            sources=sources,
            running_containers=running_containers,
        )
        if isinstance(compose_state, ComposeStateUnavailable):
            return compose_state

        compose_projects = {service.compose_project for service in compose_state.expected_services}
        selected_containers = [
            container
            for container in running_containers
            if container.compose_labels.get("com.docker.compose.project") in compose_projects
        ]
        warnings = [
            ProjectRuntimeWarning(
                warning_code=str(warning.warning_type),
                container_name=warning.container_name,
                message=warning.message,
            )
            for warning in compose_state.warnings
        ]
        containers: list[ProjectRuntimeContainer] = []
        for container in sorted(selected_containers, key=lambda item: item.container_name):
            detail = container_details.get(container.container_name)
            if detail is None:
                warnings.append(
                    ProjectRuntimeWarning(
                        warning_code="container_detail_unavailable",
                        container_name=container.container_name,
                        message="Container detail was unavailable; env values are omitted.",
                    )
                )
            containers.append(self._describe_container_runtime(container, detail))

        return ProjectRuntimeInspection(
            project_name=project_name,
            compose_project=compose_state.compose_project,
            containers=containers,
            warnings=warnings,
        )

    @classmethod
    def _describe_container_runtime(
        cls,
        container: VpsContainerInventory,
        detail: ContainerDetail | None,
    ) -> ProjectRuntimeContainer:
        env_vars = detail.env_vars if detail is not None else []
        return ProjectRuntimeContainer(
            container_id=container.container_id,
            short_container_id=container.short_container_id,
            container_name=container.container_name,
            compose_project=container.compose_labels.get("com.docker.compose.project", ""),
            service_name=container.compose_labels.get("com.docker.compose.service"),
            image=container.image,
            docker_status=container.docker_status,
            state=container.state,
            health_status=container.health_status,
            running=container.running,
            restarting=container.restarting,
            paused=container.paused,
            dead=container.dead,
            exit_code=container.exit_code,
            error=container.error,
            restart_count=container.restart_count,
            started_at=container.started_at,
            finished_at=container.finished_at,
            created_at=container.created_at,
            restart_policy=cls._describe_restart_policy(container.restart_policy),
            compose_labels=container.compose_labels,
            ports=[cls._describe_port(port) for port in container.ports],
            network_names=container.network_names,
            mounts=[cls._describe_mount(mount) for mount in container.mounts],
            env_var_names=sorted(container.env_var_names),
            selected_env=cls._select_env_vars(env_vars),
            secret_env_names=sorted(env_var.name for env_var in env_vars if env_var.secret),
            database=cls._describe_database_env(env_vars),
        )

    @staticmethod
    def _describe_restart_policy(
        policy: ContainerRestartPolicy,
    ) -> ProjectRuntimeRestartPolicy:
        return ProjectRuntimeRestartPolicy(
            name=policy.name,
            maximum_retry_count=policy.maximum_retry_count,
        )

    @staticmethod
    def _describe_port(port: ContainerDetailPort) -> ProjectRuntimePort:
        return ProjectRuntimePort(
            private_port=port.private_port,
            host_ip=port.host_ip,
            host_port=port.host_port,
        )

    @staticmethod
    def _describe_mount(mount: ContainerDetailMount) -> ProjectRuntimeMount:
        return ProjectRuntimeMount(
            type=mount.type,
            destination=mount.destination,
            mode=mount.mode,
            rw=mount.rw,
            name=mount.name,
            source_redacted=True,
        )

    @classmethod
    def _select_env_vars(cls, env_vars: list[ContainerDetailEnvVar]) -> list[ContainerDetailEnvVar]:
        selected_names = set(SAFE_ENV_VALUE_NAMES)
        for key_group in DATABASE_ENV_KEYS.values():
            selected_names.update(key_group)
        return sorted(
            (
                env_var
                for env_var in env_vars
                if env_var.name in selected_names
                and not env_var.secret
                and not env_var.value_redacted
            ),
            key=lambda item: item.name,
        )

    @classmethod
    def _describe_database_env(
        cls,
        env_vars: list[ContainerDetailEnvVar],
    ) -> ProjectRuntimeDatabase:
        values = {
            env_var.name: env_var.value
            for env_var in env_vars
            if not env_var.secret and not env_var.value_redacted and env_var.value is not None
        }
        resolved: dict[str, str | None] = {}
        missing_keys: list[Literal["host", "port", "name", "user"]] = []
        for field_name, candidate_names in DATABASE_ENV_KEYS.items():
            value = next((values[name] for name in candidate_names if name in values), None)
            resolved[field_name] = value
            if value is None:
                missing_keys.append(field_name)  # type: ignore[arg-type]
        return ProjectRuntimeDatabase(
            host=resolved["host"],
            port=resolved["port"],
            name=resolved["name"],
            user=resolved["user"],
            missing_keys=missing_keys,
        )
