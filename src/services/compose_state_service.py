"""Compare manifest Docker source inventory with Docker Compose runtime state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from manifests.models import SourceDefinition
from services.inspection_tools_service import ContainerDetailPort, VpsContainerInventory

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"


class ComposeStateWarningType(StrEnum):
    """Stable warning codes returned by Compose state comparison."""

    EXPECTED_SERVICE_NOT_RUNNING = "expected_service_not_running"
    IMAGE_MISMATCH = "image_mismatch"
    COMPOSE_PROJECT_LABEL_MISMATCH = "compose_project_label_mismatch"
    COMPOSE_SERVICE_LABEL_MISMATCH = "compose_service_label_mismatch"
    EXPECTED_PORT_MISSING = "expected_port_missing"
    EXPECTED_MOUNT_DESTINATION_MISSING = "expected_mount_destination_missing"
    EXPECTED_VOLUME_MISSING = "expected_volume_missing"
    MULTIPLE_RUNNING_CONTAINERS = "multiple_running_containers"


@dataclass(frozen=True, slots=True)
class ComposeStateUnavailable:
    """Compose-labelled target containers were not found for this project."""

    message: str


@dataclass(frozen=True, slots=True)
class ExpectedComposeService:
    """One expected Compose service derived from a manifest target container."""

    source_key: str
    compose_project: str
    service_name: str


@dataclass(frozen=True, slots=True)
class ComposeRunningMount:
    """One redacted running mount fact."""

    type: str | None
    destination: str | None
    mode: str | None
    rw: bool | None
    name: str | None
    source_redacted: bool


@dataclass(frozen=True, slots=True)
class ComposeRunningContainer:
    """One running container matched by Docker Compose labels."""

    container_id: str
    container_name: str
    image: str | None
    docker_status: str | None
    health_status: str | None
    running: bool
    compose_labels: dict[str, str]
    service_name: str | None
    ports: list[str]
    mount_destinations: list[str]
    volume_names: list[str]
    env_var_names: list[str]
    mounts: list[ComposeRunningMount]


@dataclass(frozen=True, slots=True)
class ComposeStateWarning:
    """One deterministic expected-vs-running drift warning."""

    warning_type: ComposeStateWarningType
    service_name: str | None
    message: str
    expected: str | None = None
    actual: str | None = None
    container_name: str | None = None


@dataclass(frozen=True, slots=True)
class ComposeStateInspection:
    """Comparison result for one project."""

    project_name: str
    compose_project: str | None
    expected_services: list[ExpectedComposeService]
    running_containers: list[ComposeRunningContainer]
    warnings: list[ComposeStateWarning]


class ComposeStateService:
    """Compare manifest Docker sources with runtime Compose label facts."""

    def compare(
        self,
        *,
        project_name: str,
        sources: list[SourceDefinition],
        running_containers: list[VpsContainerInventory],
    ) -> ComposeStateInspection | ComposeStateUnavailable:
        """Return Compose state derived from manifest Compose selectors and runtime labels."""

        expected_services = self._build_expected_services(sources=sources)
        if not expected_services:
            return ComposeStateUnavailable(
                message=(
                    "No Docker manifest source declares Compose selectors for runtime comparison."
                )
            )

        compose_projects = sorted({service.compose_project for service in expected_services})
        compose_project = compose_projects[0] if len(compose_projects) == 1 else None
        running = self._filter_running_containers(
            running_containers,
            compose_projects=set(compose_projects),
        )
        warnings = self._build_warnings(
            expected_services=expected_services,
            running_containers=running,
        )
        return ComposeStateInspection(
            project_name=project_name,
            compose_project=compose_project,
            expected_services=expected_services,
            running_containers=running,
            warnings=warnings,
        )

    @staticmethod
    def _build_expected_services(
        *,
        sources: list[SourceDefinition],
    ) -> list[ExpectedComposeService]:
        services: list[ExpectedComposeService] = []
        for source in sources:
            if source.source_type != "docker":
                continue
            services.append(
                ExpectedComposeService(
                    source_key=source.source_key,
                    compose_project=str(source.compose_project),
                    service_name=str(source.compose_service),
                )
            )
        return sorted(services, key=lambda item: (item.compose_project, item.service_name))

    @classmethod
    def _filter_running_containers(
        cls,
        running_containers: list[VpsContainerInventory],
        *,
        compose_projects: set[str],
    ) -> list[ComposeRunningContainer]:
        results: list[ComposeRunningContainer] = []
        for container in running_containers:
            labels = container.compose_labels
            if labels.get(COMPOSE_PROJECT_LABEL) not in compose_projects:
                continue
            results.append(cls._create_running_container(container))
        return sorted(results, key=lambda item: item.container_name)

    @classmethod
    def _create_running_container(
        cls,
        container: VpsContainerInventory,
    ) -> ComposeRunningContainer:
        mounts = [
            ComposeRunningMount(
                type=mount.type,
                destination=mount.destination,
                mode=mount.mode,
                rw=mount.rw,
                name=mount.name,
                source_redacted=True,
            )
            for mount in container.mounts
        ]
        return ComposeRunningContainer(
            container_id=container.container_id,
            container_name=container.container_name,
            image=container.image,
            docker_status=container.docker_status,
            health_status=container.health_status,
            running=container.running,
            compose_labels=container.compose_labels,
            service_name=container.compose_labels.get(COMPOSE_SERVICE_LABEL),
            ports=sorted(cls._format_ports(container.ports)),
            mount_destinations=sorted(
                mount.destination for mount in mounts if mount.destination is not None
            ),
            volume_names=sorted(mount.name for mount in mounts if mount.name is not None),
            env_var_names=sorted(container.env_var_names),
            mounts=mounts,
        )

    @staticmethod
    def _format_ports(ports: list[ContainerDetailPort]) -> list[str]:
        results: list[str] = []
        for port in ports:
            if port.host_port is None:
                results.append(port.private_port)
                continue
            host = f"{port.host_ip}:" if port.host_ip else ""
            results.append(f"{port.private_port}->{host}{port.host_port}")
        return results

    @classmethod
    def _build_warnings(
        cls,
        *,
        expected_services: list[ExpectedComposeService],
        running_containers: list[ComposeRunningContainer],
    ) -> list[ComposeStateWarning]:
        warnings: list[ComposeStateWarning] = []
        running_by_service: dict[tuple[str, str], list[ComposeRunningContainer]] = {}
        expected_by_service = {
            (service.compose_project, service.service_name): service
            for service in expected_services
        }
        for container in running_containers:
            project = container.compose_labels.get(COMPOSE_PROJECT_LABEL)
            service = container.service_name
            if project is None or service is None or not container.running:
                continue
            running_by_service.setdefault((project, service), []).append(container)

        for key, expected in expected_by_service.items():
            matched = running_by_service.get(key, [])
            if not matched:
                warnings.append(
                    ComposeStateWarning(
                        warning_type=ComposeStateWarningType.EXPECTED_SERVICE_NOT_RUNNING,
                        service_name=expected.service_name,
                        message="Expected Compose service is not currently running.",
                    )
                )
                continue
            if len(matched) > 1:
                warnings.append(
                    ComposeStateWarning(
                        warning_type=ComposeStateWarningType.MULTIPLE_RUNNING_CONTAINERS,
                        service_name=expected.service_name,
                        message="Multiple running containers match one expected service.",
                        actual=str(len(matched)),
                    )
                )
            warnings.extend(cls._compare_expected_service(expected, matched[0]))

        return sorted(warnings, key=lambda item: (item.warning_type, item.service_name or ""))

    @staticmethod
    def _compare_expected_service(
        expected: ExpectedComposeService,
        running: ComposeRunningContainer,
    ) -> list[ComposeStateWarning]:
        warnings: list[ComposeStateWarning] = []
        actual_project = running.compose_labels.get(COMPOSE_PROJECT_LABEL)
        if actual_project != expected.compose_project:
            warnings.append(
                ComposeStateWarning(
                    warning_type=ComposeStateWarningType.COMPOSE_PROJECT_LABEL_MISMATCH,
                    service_name=expected.service_name,
                    message="Compose project label differs from the manifest service identity.",
                    expected=expected.compose_project,
                    actual=actual_project,
                    container_name=running.container_name,
                )
            )
        if running.service_name != expected.service_name:
            warnings.append(
                ComposeStateWarning(
                    warning_type=ComposeStateWarningType.COMPOSE_SERVICE_LABEL_MISMATCH,
                    service_name=expected.service_name,
                    message="Compose service label differs from the manifest service identity.",
                    expected=expected.service_name,
                    actual=running.service_name,
                    container_name=running.container_name,
                )
            )
        return warnings
