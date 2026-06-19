"""Inspect project deployment and image provenance without mutating runtime state."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from manifests.models import SourceDefinition
from services.compose_state_service import ComposeStateService, ComposeStateUnavailable
from services.inspection_tools_service import VpsContainerInventory

MAX_TAG_FILE_BYTES = 1024


class DeploymentCurrentTag(BaseModel):
    """Current expected deployment tag read from bounded project metadata."""

    model_config = ConfigDict(extra="forbid")

    path: str | None
    value: str | None
    status: str
    error: str | None


class DeploymentExpectedService(BaseModel):
    """Expected service identity derived from manifest/runtime labels."""

    model_config = ConfigDict(extra="forbid")

    source_key: str
    compose_project: str | None
    service_name: str
    expected_image_repository: str | None


class DeploymentRunningContainer(BaseModel):
    """Running image facts for one Compose-labelled container."""

    model_config = ConfigDict(extra="forbid")

    container_id: str
    short_container_id: str
    container_name: str
    compose_project: str
    service_name: str | None
    image: str | None
    image_repository: str | None
    image_tag: str | None
    image_digest: str | None
    docker_status: str | None
    running: bool
    created_at: str | None
    started_at: str | None


class DeploymentWarning(BaseModel):
    """One deterministic deployment provenance warning."""

    model_config = ConfigDict(extra="forbid")

    warning_code: str
    service_name: str | None
    container_name: str | None
    message: str
    expected: str | None = None
    actual: str | None = None


class ProjectDeploymentInspection(BaseModel):
    """Deployment provenance for one project."""

    model_config = ConfigDict(extra="forbid")

    project_name: str
    compose_project: str | None
    compose_files: list[str]
    current_tag: DeploymentCurrentTag
    expected_services: list[DeploymentExpectedService]
    running_containers: list[DeploymentRunningContainer]
    warnings: list[DeploymentWarning]


class ProjectDeploymentService:
    """Compare configured deployment expectations with Docker runtime facts."""

    def __init__(self, compose_state_service: ComposeStateService | None = None) -> None:
        self.compose_state_service = compose_state_service or ComposeStateService()

    def inspect(
        self,
        *,
        project_name: str,
        sources: list[SourceDefinition],
        compose_files: list[str],
        current_tag_path: str | None,
        expected_image_repositories: dict[str, str],
        running_containers: list[VpsContainerInventory],
    ) -> ProjectDeploymentInspection | ComposeStateUnavailable:
        """Return expected-vs-running deployment facts for one project."""

        compose_state = self.compose_state_service.compare(
            project_name=project_name,
            sources=sources,
            running_containers=running_containers,
        )
        if isinstance(compose_state, ComposeStateUnavailable) and not (expected_image_repositories):
            return compose_state

        current_tag = self._read_current_tag(current_tag_path)
        if isinstance(compose_state, ComposeStateUnavailable):
            expected_services = [
                DeploymentExpectedService(
                    source_key=service_name,
                    compose_project=None,
                    service_name=service_name,
                    expected_image_repository=repository,
                )
                for service_name, repository in sorted(expected_image_repositories.items())
            ]
            compose_project = None
        else:
            expected_services = [
                DeploymentExpectedService(
                    source_key=service.source_key,
                    compose_project=service.compose_project,
                    service_name=service.service_name,
                    expected_image_repository=expected_image_repositories.get(service.service_name),
                )
                for service in compose_state.expected_services
            ]
            compose_project = compose_state.compose_project
        compose_projects = {
            service.compose_project
            for service in expected_services
            if service.compose_project is not None
        }
        running = [
            self._describe_running_container(container)
            for container in running_containers
            if container.compose_labels.get("com.docker.compose.project") in compose_projects
        ]
        warnings = self._describe_warnings(
            expected_services=expected_services,
            running_containers=running,
            current_tag=current_tag,
        )

        return ProjectDeploymentInspection(
            project_name=project_name,
            compose_project=compose_project,
            compose_files=compose_files,
            current_tag=current_tag,
            expected_services=expected_services,
            running_containers=sorted(running, key=lambda item: item.container_name),
            warnings=warnings,
        )

    @staticmethod
    def _read_current_tag(path: str | None) -> DeploymentCurrentTag:
        if path is None:
            return DeploymentCurrentTag(
                path=None,
                value=None,
                status="not_configured",
                error=None,
            )
        try:
            tag_path = Path(path)
            if not tag_path.exists():
                return DeploymentCurrentTag(
                    path=path,
                    value=None,
                    status="missing",
                    error="Current tag file was not found.",
                )
            if tag_path.stat().st_size > MAX_TAG_FILE_BYTES:
                return DeploymentCurrentTag(
                    path=path,
                    value=None,
                    status="too_large",
                    error="Current tag file exceeded the maximum allowed size.",
                )
            content = tag_path.read_text(encoding="utf-8")[:MAX_TAG_FILE_BYTES]
        except OSError as error:
            return DeploymentCurrentTag(
                path=path,
                value=None,
                status="error",
                error=str(error),
            )
        value = content.strip().splitlines()[0] if content.strip() else None
        return DeploymentCurrentTag(
            path=path,
            value=value,
            status="ok" if value else "empty",
            error=None if value else "Current tag file was empty.",
        )

    @classmethod
    def _describe_running_container(
        cls,
        container: VpsContainerInventory,
    ) -> DeploymentRunningContainer:
        repository, tag, digest = cls._split_image(container.image)
        return DeploymentRunningContainer(
            container_id=container.container_id,
            short_container_id=container.short_container_id,
            container_name=container.container_name,
            compose_project=container.compose_labels.get("com.docker.compose.project", ""),
            service_name=container.compose_labels.get("com.docker.compose.service"),
            image=container.image,
            image_repository=repository,
            image_tag=tag,
            image_digest=digest,
            docker_status=container.docker_status,
            running=container.running,
            created_at=container.created_at,
            started_at=container.started_at,
        )

    @staticmethod
    def _split_image(image: str | None) -> tuple[str | None, str | None, str | None]:
        if image is None:
            return None, None, None
        image_without_digest, separator, digest = image.partition("@")
        digest_value = digest if separator else None
        last_slash_index = image_without_digest.rfind("/")
        last_colon_index = image_without_digest.rfind(":")
        if last_colon_index > last_slash_index:
            return (
                image_without_digest[:last_colon_index],
                image_without_digest[last_colon_index + 1 :],
                digest_value,
            )
        return image_without_digest, None, digest_value

    @classmethod
    def _describe_warnings(
        cls,
        *,
        expected_services: list[DeploymentExpectedService],
        running_containers: list[DeploymentRunningContainer],
        current_tag: DeploymentCurrentTag,
    ) -> list[DeploymentWarning]:
        warnings: list[DeploymentWarning] = []
        active_containers = [container for container in running_containers if container.running]
        running_by_service = {container.service_name: container for container in active_containers}
        for expected_service in expected_services:
            container = running_by_service.get(expected_service.service_name)
            if container is None:
                warnings.append(
                    DeploymentWarning(
                        warning_code="expected_service_not_running",
                        service_name=expected_service.service_name,
                        container_name=None,
                        message="Expected Compose service has no running container.",
                    )
                )
                continue
            if (
                expected_service.expected_image_repository is not None
                and container.image_repository != expected_service.expected_image_repository
            ):
                warnings.append(
                    DeploymentWarning(
                        warning_code="image_repository_mismatch",
                        service_name=expected_service.service_name,
                        container_name=container.container_name,
                        message="Running image repository does not match configured expectation.",
                        expected=expected_service.expected_image_repository,
                        actual=container.image_repository,
                    )
                )
            if (
                expected_service.expected_image_repository is not None
                and current_tag.value is not None
                and container.image_tag != current_tag.value
            ):
                warnings.append(
                    DeploymentWarning(
                        warning_code="image_tag_mismatch",
                        service_name=expected_service.service_name,
                        container_name=container.container_name,
                        message="Running image tag does not match current deployment tag.",
                        expected=current_tag.value,
                        actual=container.image_tag,
                    )
                )
        if current_tag.status not in {"ok", "not_configured"}:
            warnings.append(
                DeploymentWarning(
                    warning_code=f"current_tag_{current_tag.status}",
                    service_name=None,
                    container_name=None,
                    message=current_tag.error or "Current deployment tag is unavailable.",
                    expected=None,
                    actual=None,
                )
            )
        return warnings
