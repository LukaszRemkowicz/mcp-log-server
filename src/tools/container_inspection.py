"""Read-only MCP tools for manifest-whitelisted container inspection.

These tools are the specialist container-inspection surface for agents that
need to verify deployed project files or container runtime status inside
approved containers.

Important boundary:

- agents do not get arbitrary container exec
- agents do not get broad filesystem browsing
- agents choose a high-level inspection operation
- server code maps that operation to a fixed internal command set
- requested paths must stay inside manifest-approved prefixes for file tools

So this module is intentionally narrower than "run a command in a container".
It exposes only deterministic, read-only inspection primitives:

- `read_container_file`
- `list_container_directory`
- `inspect_containers_health`
- `inspect_container_detail`
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

from fastmcp.server.dependencies import get_http_request
from fastmcp.tools.base import ToolResult
from mcp.types import ToolAnnotations

from auth.mcp_authorized_manifests import AuthorizedProjectManifests
from auth.scopes import CONTAINER_FILES_READ_SCOPE
from conf import settings
from decorators import project_authorized_tool, workflow_discoverable_tool
from logging_config import get_logger
from manifests.models import Manifest, SourceDefinition
from services.compose_state_service import (
    ComposeRunningContainer,
    ComposeRunningMount,
    ComposeStateInspection,
    ComposeStateService,
    ComposeStateUnavailable,
    ComposeStateWarning,
    ExpectedComposeService,
)
from services.inspection_tools_service import (
    MAX_CONTAINER_FILE_BYTES,
    MAX_VPS_CONTAINERS,
    MAX_VPS_VOLUMES,
    ContainerDetail,
    ContainerHealth,
    ContainerPathStat,
    InspectionToolsService,
    InspectionToolsServiceError,
    VpsContainerInventory,
    VpsVolumeInventory,
)
from services.project_manifest import ProjectManifestError, ProjectManifestService
from tools.agent_hints import (
    INSPECT_CONTAINER_DETAIL_TOOL_DESCRIPTION,
    INSPECT_CONTAINERS_HEALTH_TOOL_DESCRIPTION,
    INSPECT_PROJECT_COMPOSE_STATE_TOOL_DESCRIPTION,
    INSPECT_VPS_CONTAINERS_TOOL_DESCRIPTION,
    INSPECT_VPS_VOLUMES_TOOL_DESCRIPTION,
    LIST_CONTAINER_DIRECTORY_TOOL_DESCRIPTION,
    READ_CONTAINER_FILE_TOOL_DESCRIPTION,
    STAT_CONTAINER_PATH_TOOL_DESCRIPTION,
)
from tools.errors import build_container_inspection_error_result
from tools.models import (
    ComposeRunningContainerPayload,
    ComposeRunningMountPayload,
    ComposeStateWarningPayload,
    ContainerDetailEnvVarPayload,
    ContainerDetailMountPayload,
    ContainerDetailNetworkPayload,
    ContainerDetailPortPayload,
    ContainerHealthPayload,
    ContainerPathMetadataPayload,
    ContainerRestartPolicyPayload,
    ExpectedComposeServicePayload,
    InspectContainerDetailPayload,
    InspectContainersHealthPayload,
    InspectProjectComposeStatePayload,
    InspectVpsContainersPayload,
    InspectVpsVolumesPayload,
    ListContainerDirectoryPayload,
    ReadContainerFilePayload,
    StatContainerPathPayload,
    VpsContainerInventoryPayload,
    VpsVolumeInventoryPayload,
)

logger: logging.Logger = get_logger("tools.container_inspection")
manifest_service = ProjectManifestService()
inspection_tools_service = InspectionToolsService()
compose_state_service = ComposeStateService()
READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
)


def _get_authorized_manifest(project_name: str) -> Manifest | None:
    """Return one request-state manifest prepared by AuthorizedManifestsMiddleware."""

    request = get_http_request()
    authorized_manifests = cast(
        AuthorizedProjectManifests,
        request.state.authorized_manifests,
    )
    return authorized_manifests.manifests.get(project_name)


def _build_unknown_project_manifest_error(project_name: str) -> ProjectManifestError:
    """Return the standard missing-manifest error for this tool module."""

    return ProjectManifestError(
        message=(
            f"Unknown project {project_name!r}. No persisted manifest was found for that project."
        )
    )


@dataclass(frozen=True, slots=True)
class ContainerInspectionContext:
    """Resolved project/source/path context shared by container inspection tools."""

    project_name: str
    definition: SourceDefinition
    normalized_path: str


@dataclass(frozen=True, slots=True)
class ContainerSourceContext:
    """Resolved project/source context for container-level inspection tools."""

    project_name: str
    definition: SourceDefinition


async def _prepare_container_source_context(
    *,
    action: str,
    project_name: str,
    source_key: str,
) -> ContainerSourceContext | ToolResult:
    """Resolve manifest and docker source for one container-level tool."""

    manifest = _get_authorized_manifest(project_name)
    if manifest is None:
        manifest_error = _build_unknown_project_manifest_error(project_name)
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": action,
                "error_message": manifest_error.message,
                "source_key": source_key,
                "project_name": project_name,
            },
        )
        return build_container_inspection_error_result(
            action=action,
            message=manifest_error.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=None,
            settings=settings,
        )

    definition = manifest_service.get_container_source_or_error(
        manifest,
        source_key,
    )
    if isinstance(definition, ProjectManifestError):
        return build_container_inspection_error_result(
            action=action,
            message=definition.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=None,
            settings=settings,
        )

    return ContainerSourceContext(
        project_name=manifest.project_key,
        definition=definition,
    )


async def _prepare_container_inspection_context(
    *,
    action: str,
    project_name: str,
    source_key: str,
    path: str,
) -> ContainerInspectionContext | ToolResult:
    """Resolve manifest, container source, and allowed path for one inspection tool."""

    manifest = _get_authorized_manifest(project_name)
    if manifest is None:
        manifest_error = _build_unknown_project_manifest_error(project_name)
        logger.info(
            "tool error",
            extra={
                "event": "tool_error",
                "tool_name": action,
                "error_message": manifest_error.message,
                "source_key": source_key,
                "path": path,
                "project_name": project_name,
            },
        )
        return build_container_inspection_error_result(
            action=action,
            message=manifest_error.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    definition = manifest_service.get_container_source_or_error(
        manifest,
        source_key,
    )
    if isinstance(definition, ProjectManifestError):
        return build_container_inspection_error_result(
            action=action,
            message=definition.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    normalized_path = inspection_tools_service.normalize_container_path_or_error(path)
    if isinstance(normalized_path, InspectionToolsServiceError):
        return build_container_inspection_error_result(
            action=action,
            message=normalized_path.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    if not inspection_tools_service.container_path_is_allowed(definition, normalized_path):
        return build_container_inspection_error_result(
            action=action,
            message=(
                "Requested container path is outside the manifest whitelist "
                "for the selected source."
            ),
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    return ContainerInspectionContext(
        project_name=manifest.project_key,
        definition=definition,
        normalized_path=normalized_path,
    )


async def _prepare_container_directory_context(
    *,
    action: str,
    project_name: str,
    source_key: str,
    path: str | None,
) -> ContainerInspectionContext | ToolResult:
    """Resolve manifest, container source, and allowed directory-listing path."""

    source_context = await _prepare_container_source_context(
        action=action,
        project_name=project_name,
        source_key=source_key,
    )
    if isinstance(source_context, ToolResult):
        return source_context

    normalized_path = inspection_tools_service.resolve_container_directory_path_or_error(
        source_context.definition,
        path,
    )
    if isinstance(normalized_path, InspectionToolsServiceError):
        return build_container_inspection_error_result(
            action=action,
            message=normalized_path.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    if not inspection_tools_service.container_path_is_allowed(
        source_context.definition, normalized_path
    ):
        return build_container_inspection_error_result(
            action=action,
            message=(
                "Requested container path is outside the manifest whitelist "
                "for the selected source."
            ),
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    return ContainerInspectionContext(
        project_name=source_context.project_name,
        definition=source_context.definition,
        normalized_path=normalized_path,
    )


def create_container_payload(
    stat_payload: ContainerPathStat,
) -> ContainerPathMetadataPayload:
    """Convert one Docker path stat result into the MCP path metadata payload.

    `InspectionToolsService` returns infrastructure-focused `ContainerPathStat` objects.
    The MCP tool response uses `ContainerPathMetadataPayload`, so this adapter
    keeps that model conversion explicit at the tool boundary.
    """

    return ContainerPathMetadataPayload(
        path=stat_payload.path,
        name=PurePosixPath(stat_payload.path).name or stat_payload.path,
        is_dir=stat_payload.is_dir,
        size=stat_payload.size,
        mode=stat_payload.mode,
        modified_at=(
            str(stat_payload.modified_at) if stat_payload.modified_at is not None else None
        ),
    )


def create_container_health_item_payload(
    health: ContainerHealth,
    *,
    source_key: str,
) -> ContainerHealthPayload:
    """Convert Docker runtime state into one MCP health item."""

    return ContainerHealthPayload(
        source_key=source_key,
        inspection_status="ok",
        inspection_error=None,
        container_name=health.container_name,
        container_id=health.container_id,
        image=health.image,
        docker_status=health.docker_status,
        health_status=health.health_status,
        running=health.running,
        restarting=health.restarting,
        paused=health.paused,
        dead=health.dead,
        exit_code=health.exit_code,
        error=health.error,
        restart_count=health.restart_count,
        started_at=health.started_at,
        finished_at=health.finished_at,
    )


def create_container_health_error_item_payload(
    error: InspectionToolsServiceError,
    *,
    source_key: str,
    container_name: str,
) -> ContainerHealthPayload:
    """Represent one failed container health lookup without failing the whole overview."""

    return ContainerHealthPayload(
        source_key=source_key,
        inspection_status="error",
        inspection_error=error.message,
        container_name=container_name,
        container_id="",
        image=None,
        docker_status=None,
        health_status=None,
        running=False,
        restarting=False,
        paused=False,
        dead=False,
        exit_code=None,
        error=None,
        restart_count=None,
        started_at=None,
        finished_at=None,
    )


def create_container_detail_payload(
    detail: ContainerDetail,
    *,
    project_name: str,
    source_key: str,
) -> InspectContainerDetailPayload:
    """Convert curated Docker inspect metadata into the MCP detail response."""

    return InspectContainerDetailPayload(
        action="inspect_container_detail",
        project_name=project_name,
        source_key=source_key,
        container=create_container_health_item_payload(
            detail.health,
            source_key=source_key,
        ),
        created_at=detail.created_at,
        env_var_names=detail.env_var_names,
        env_vars=[
            ContainerDetailEnvVarPayload(
                name=item.name,
                value=item.value,
                value_redacted=item.value_redacted,
                secret=item.secret,
            )
            for item in detail.env_vars
        ],
        label_keys=detail.label_keys,
        compose_labels=detail.compose_labels,
        restart_policy=ContainerRestartPolicyPayload(
            name=detail.restart_policy.name,
            maximum_retry_count=detail.restart_policy.maximum_retry_count,
        ),
        command=detail.command,
        entrypoint=detail.entrypoint,
        working_dir=detail.working_dir,
        user=detail.user,
        ports=[
            ContainerDetailPortPayload(
                private_port=item.private_port,
                host_ip=item.host_ip,
                host_port=item.host_port,
            )
            for item in detail.ports
        ],
        mounts=[
            ContainerDetailMountPayload(
                type=item.type,
                destination=item.destination,
                mode=item.mode,
                rw=item.rw,
            )
            for item in detail.mounts
        ],
        networks=[
            ContainerDetailNetworkPayload(
                name=item.name,
                ip_address=item.ip_address,
                aliases=item.aliases,
            )
            for item in detail.networks
        ],
        health_log=detail.health_log,
    )


def create_vps_container_inventory_payload(
    container: VpsContainerInventory,
) -> VpsContainerInventoryPayload:
    """Convert Docker ps-style service inventory into one MCP response row."""

    return VpsContainerInventoryPayload(
        container_id=container.container_id,
        short_container_id=container.short_container_id,
        container_name=container.container_name,
        image=container.image,
        command=container.command,
        command_preview=container.command_preview,
        created_at=container.created_at,
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
        compose_labels=container.compose_labels,
        restart_policy=ContainerRestartPolicyPayload(
            name=container.restart_policy.name,
            maximum_retry_count=container.restart_policy.maximum_retry_count,
        ),
        ports=[
            ContainerDetailPortPayload(
                private_port=item.private_port,
                host_ip=item.host_ip,
                host_port=item.host_port,
            )
            for item in container.ports
        ],
        network_names=container.network_names,
        triage_notes=container.triage_notes,
    )


def create_vps_volume_inventory_payload(
    volume: VpsVolumeInventory,
) -> VpsVolumeInventoryPayload:
    """Convert Docker volume service inventory into one MCP response row."""

    return VpsVolumeInventoryPayload(
        volume_name=volume.volume_name,
        driver=volume.driver,
        scope=volume.scope,
        created_at=volume.created_at,
        compose_labels=volume.compose_labels,
        option_keys=volume.option_keys,
        mountpoint_available=volume.mountpoint_available,
        mountpoint_redacted=volume.mountpoint_redacted,
        usage_ref_count=volume.usage_ref_count,
        usage_size_bytes=volume.usage_size_bytes,
    )


def create_expected_compose_service_payload(
    service: ExpectedComposeService,
) -> ExpectedComposeServicePayload:
    """Convert inferred Compose service identity into an MCP payload row."""

    return ExpectedComposeServicePayload(
        source_key=service.source_key,
        compose_project=service.compose_project,
        service_name=service.service_name,
    )


def create_compose_running_mount_payload(
    mount: ComposeRunningMount,
) -> ComposeRunningMountPayload:
    """Convert redacted running mount metadata into an MCP payload row."""

    return ComposeRunningMountPayload(
        type=mount.type,
        destination=mount.destination,
        mode=mount.mode,
        rw=mount.rw,
        name=mount.name,
        source_redacted=mount.source_redacted,
    )


def create_compose_running_container_payload(
    container: ComposeRunningContainer,
) -> ComposeRunningContainerPayload:
    """Convert one matched running container into an MCP payload row."""

    return ComposeRunningContainerPayload(
        container_id=container.container_id,
        container_name=container.container_name,
        image=container.image,
        docker_status=container.docker_status,
        health_status=container.health_status,
        running=container.running,
        compose_labels=container.compose_labels,
        service_name=container.service_name,
        ports=container.ports,
        mount_destinations=container.mount_destinations,
        volume_names=container.volume_names,
        env_var_names=container.env_var_names,
        mounts=[create_compose_running_mount_payload(mount) for mount in container.mounts],
    )


def create_compose_state_warning_payload(
    warning: ComposeStateWarning,
) -> ComposeStateWarningPayload:
    """Convert one drift warning into an MCP payload row."""

    return ComposeStateWarningPayload(
        warning_type=warning.warning_type,
        service_name=warning.service_name,
        message=warning.message,
        expected=warning.expected,
        actual=warning.actual,
        container_name=warning.container_name,
    )


def create_project_compose_state_payload(
    inspection: ComposeStateInspection,
) -> InspectProjectComposeStatePayload:
    """Convert Compose runtime state into the MCP response payload."""

    return InspectProjectComposeStatePayload(
        action="inspect_project_compose_state",
        project_name=inspection.project_name,
        compose_project=inspection.compose_project,
        expected_services=[
            create_expected_compose_service_payload(service)
            for service in inspection.expected_services
        ],
        running_containers=[
            create_compose_running_container_payload(container)
            for container in inspection.running_containers
        ],
        warnings=[create_compose_state_warning_payload(warning) for warning in inspection.warnings],
    )


@workflow_discoverable_tool(
    CONTAINER_FILES_READ_SCOPE,
    mcp_description=INSPECT_VPS_CONTAINERS_TOOL_DESCRIPTION,
    annotations=READ_ONLY_TOOL_ANNOTATIONS,
)
async def inspect_vps_containers() -> ToolResult:
    """Return a bounded Docker ps-style inventory for visible VPS containers."""

    containers = inspection_tools_service.inspect_vps_containers()
    if isinstance(containers, InspectionToolsServiceError):
        return build_container_inspection_error_result(
            action="inspect_vps_containers",
            message=containers.message,
            requested_project_name=None,
            source_key=None,
            path=None,
            settings=settings,
        )

    payload = InspectVpsContainersPayload(
        action="inspect_vps_containers",
        container_count=len(containers),
        truncated=len(containers) >= MAX_VPS_CONTAINERS,
        containers=[create_vps_container_inventory_payload(item) for item in containers],
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "inspect_vps_containers",
            "container_count": payload.container_count,
            "truncated": payload.truncated,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(
    CONTAINER_FILES_READ_SCOPE,
    mcp_description=INSPECT_VPS_VOLUMES_TOOL_DESCRIPTION,
    annotations=READ_ONLY_TOOL_ANNOTATIONS,
)
async def inspect_vps_volumes(
    dangling_only: bool = False,
    anonymous_only: bool = False,
    name_prefix: str | None = None,
) -> ToolResult:
    """Return a bounded Docker volume ls-style inventory for visible VPS volumes."""

    volumes = inspection_tools_service.inspect_vps_volumes(
        dangling_only=dangling_only,
        anonymous_only=anonymous_only,
        name_prefix=name_prefix,
    )
    if isinstance(volumes, InspectionToolsServiceError):
        return build_container_inspection_error_result(
            action="inspect_vps_volumes",
            message=volumes.message,
            requested_project_name=None,
            source_key=None,
            path=None,
            settings=settings,
        )

    payload = InspectVpsVolumesPayload(
        action="inspect_vps_volumes",
        filters={
            "dangling_only": dangling_only,
            "anonymous_only": anonymous_only,
            "name_prefix": name_prefix,
        },
        volume_count=len(volumes),
        truncated=len(volumes) >= MAX_VPS_VOLUMES,
        volumes=[create_vps_volume_inventory_payload(item) for item in volumes],
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "inspect_vps_volumes",
            "volume_count": payload.volume_count,
            "truncated": payload.truncated,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(
    CONTAINER_FILES_READ_SCOPE,
    mcp_description=INSPECT_PROJECT_COMPOSE_STATE_TOOL_DESCRIPTION,
    annotations=READ_ONLY_TOOL_ANNOTATIONS,
)
@project_authorized_tool
async def inspect_project_compose_state(
    project_name: str | None = None,
) -> ToolResult:
    """Inspect Compose state by matching manifest targets to Docker labels."""

    assert project_name is not None
    manifest = _get_authorized_manifest(project_name)
    if manifest is None:
        manifest_error = _build_unknown_project_manifest_error(project_name)
        return build_container_inspection_error_result(
            action="inspect_project_compose_state",
            message=manifest_error.message,
            requested_project_name=project_name,
            source_key=None,
            path=None,
            settings=settings,
        )

    containers = inspection_tools_service.inspect_vps_containers()
    if isinstance(containers, InspectionToolsServiceError):
        return build_container_inspection_error_result(
            action="inspect_project_compose_state",
            message=containers.message,
            requested_project_name=project_name,
            source_key=None,
            path=None,
            settings=settings,
        )

    inspection = compose_state_service.compare(
        project_name=manifest.project_key,
        sources=manifest.sources,
        running_containers=containers,
    )
    if isinstance(inspection, ComposeStateUnavailable):
        return build_container_inspection_error_result(
            action="inspect_project_compose_state",
            message=inspection.message,
            requested_project_name=project_name,
            source_key=None,
            path=None,
            settings=settings,
        )

    payload = create_project_compose_state_payload(inspection)
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "inspect_project_compose_state",
            "project_name": payload.project_name,
            "expected_service_count": len(payload.expected_services),
            "running_container_count": len(payload.running_containers),
            "warning_count": len(payload.warnings),
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(
    CONTAINER_FILES_READ_SCOPE,
    mcp_description=INSPECT_CONTAINERS_HEALTH_TOOL_DESCRIPTION,
    annotations=READ_ONLY_TOOL_ANNOTATIONS,
)
@project_authorized_tool
async def inspect_containers_health(
    project_name: str | None = None,
) -> ToolResult:
    """Return Docker runtime status for all manifest-approved source containers."""

    assert project_name is not None
    manifest = _get_authorized_manifest(project_name)
    if manifest is None:
        manifest_error = _build_unknown_project_manifest_error(project_name)
        return build_container_inspection_error_result(
            action="inspect_containers_health",
            message=manifest_error.message,
            requested_project_name=project_name,
            source_key=None,
            path=None,
            settings=settings,
        )

    docker_sources = [source for source in manifest.sources if source.source_type == "docker"]
    container_payloads: list[ContainerHealthPayload] = []
    for source in docker_sources:
        health = inspection_tools_service.inspect_container_health(source.target)
        if isinstance(health, InspectionToolsServiceError):
            container_payloads.append(
                create_container_health_error_item_payload(
                    health,
                    source_key=source.source_key,
                    container_name=source.target,
                )
            )
            continue
        container_payloads.append(
            create_container_health_item_payload(
                health,
                source_key=source.source_key,
            )
        )

    payload = InspectContainersHealthPayload(
        action="inspect_containers_health",
        project_name=manifest.project_key,
        resolved_source_keys=[source.source_key for source in docker_sources],
        containers=container_payloads,
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "inspect_containers_health",
            "project_name": payload.project_name,
            "container_count": len(payload.containers),
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(
    CONTAINER_FILES_READ_SCOPE,
    mcp_description=INSPECT_CONTAINER_DETAIL_TOOL_DESCRIPTION,
    annotations=READ_ONLY_TOOL_ANNOTATIONS,
)
@project_authorized_tool
async def inspect_container_detail(
    source_key: str,
    project_name: str | None = None,
) -> ToolResult:
    """Return curated Docker inspect metadata for one manifest-approved container."""

    assert project_name is not None
    context = await _prepare_container_source_context(
        action="inspect_container_detail",
        project_name=project_name,
        source_key=source_key,
    )
    if isinstance(context, ToolResult):
        return context

    detail = inspection_tools_service.inspect_container_detail(context.definition.target)
    if isinstance(detail, InspectionToolsServiceError):
        return build_container_inspection_error_result(
            action="inspect_container_detail",
            message=detail.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=None,
            settings=settings,
        )

    payload = create_container_detail_payload(
        detail,
        project_name=context.project_name,
        source_key=context.definition.source_key,
    )
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "inspect_container_detail",
            "source_key": payload.source_key,
            "container_name": payload.container.container_name,
            "docker_status": payload.container.docker_status,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(
    CONTAINER_FILES_READ_SCOPE,
    mcp_description=STAT_CONTAINER_PATH_TOOL_DESCRIPTION,
    annotations=READ_ONLY_TOOL_ANNOTATIONS,
)
@project_authorized_tool
async def stat_container_path(
    source_key: str,
    path: str,
    project_name: str | None = None,
) -> ToolResult:
    """Return metadata for one approved container file or directory path."""

    assert project_name is not None
    context = await _prepare_container_inspection_context(
        action="stat_container_path",
        project_name=project_name,
        source_key=source_key,
        path=path,
    )
    if isinstance(context, ToolResult):
        return context

    stat_payload = inspection_tools_service.stat_container_path(
        context.definition.target,
        context.normalized_path,
    )
    if isinstance(stat_payload, InspectionToolsServiceError):
        return build_container_inspection_error_result(
            action="stat_container_path",
            message=stat_payload.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    payload = StatContainerPathPayload(
        action="stat_container_path",
        requested_project_name=project_name,
        project_name=context.project_name,
        source_key=context.definition.source_key,
        container_name=context.definition.target,
        path=context.normalized_path,
        file=create_container_payload(stat_payload),
    )

    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "stat_container_path",
            "source_key": payload.source_key,
            "container_name": payload.container_name,
            "path": payload.path,
            "is_dir": payload.file.is_dir,
            "size": payload.file.size,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(
    CONTAINER_FILES_READ_SCOPE,
    mcp_description=READ_CONTAINER_FILE_TOOL_DESCRIPTION,
    annotations=READ_ONLY_TOOL_ANNOTATIONS,
)
@project_authorized_tool
async def read_container_file(
    source_key: str,
    path: str,
    project_name: str | None = None,
    max_bytes: int = MAX_CONTAINER_FILE_BYTES,
) -> ToolResult:
    """Read one approved text file from a docker source container.

    Use this when an agent needs the actual file contents, for example to
    verify deployed application code or inspect a runtime config file.

    The tool is intentionally read-only and bounded:

    - it rejects directories
    - it enforces manifest-approved path prefixes
    - it limits returned bytes with `max_bytes`
    - it reports whether the returned content was truncated

    This keeps the public surface deterministic and safer than exposing
    arbitrary container exec to agents.
    """

    assert project_name is not None
    context = await _prepare_container_inspection_context(
        action="read_container_file",
        project_name=project_name,
        source_key=source_key,
        path=path,
    )
    if isinstance(context, ToolResult):
        return context

    stat_payload = inspection_tools_service.stat_container_path(
        context.definition.target,
        context.normalized_path,
    )
    if isinstance(stat_payload, InspectionToolsServiceError):
        return build_container_inspection_error_result(
            action="read_container_file",
            message=stat_payload.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )

    read_result = inspection_tools_service.read_container_file(
        context.definition.target,
        context.normalized_path,
        max_bytes=max_bytes,
    )
    if isinstance(read_result, InspectionToolsServiceError):
        return build_container_inspection_error_result(
            action="read_container_file",
            message=read_result.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )
    content, truncated = read_result
    payload = ReadContainerFilePayload(
        action="read_container_file",
        requested_project_name=project_name,
        project_name=context.project_name,
        source_key=context.definition.source_key,
        container_name=context.definition.target,
        path=context.normalized_path,
        max_bytes=max_bytes,
        truncated=truncated,
        content=content,
        file=create_container_payload(stat_payload),
    )

    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "read_container_file",
            "source_key": payload.source_key,
            "container_name": payload.container_name,
            "path": payload.path,
            "truncated": payload.truncated,
            "size": payload.file.size,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@workflow_discoverable_tool(
    CONTAINER_FILES_READ_SCOPE,
    mcp_description=LIST_CONTAINER_DIRECTORY_TOOL_DESCRIPTION,
    annotations=READ_ONLY_TOOL_ANNOTATIONS,
)
@project_authorized_tool
async def list_container_directory(
    source_key: str,
    path: str | None = None,
    project_name: str | None = None,
) -> ToolResult:
    """List files/directories inside an approved container directory.

    Use this as the navigation starting point for a source container. If
    `path` is omitted or blank, the tool lists the source's first
    manifest-approved inspection prefix, usually the main project folder such
    as `/app/`, similar to starting with `ls -la` in a terminal. After seeing
    that structure, pass an explicit child directory path to keep drilling down.

    The behavior is intentionally narrow:

    - only one directory at a time
    - only immediate children
    - no recursive crawl
    - same manifest/JWT/path safety checks as the other inspection tools
    """

    assert project_name is not None
    context = await _prepare_container_directory_context(
        action="list_container_directory",
        project_name=project_name,
        source_key=source_key,
        path=path,
    )
    if isinstance(context, ToolResult):
        return context

    list_result = inspection_tools_service.list_container_directory(
        context.definition.target,
        context.normalized_path,
    )
    if isinstance(list_result, InspectionToolsServiceError):
        return build_container_inspection_error_result(
            action="list_container_directory",
            message=list_result.message,
            requested_project_name=project_name,
            source_key=source_key,
            path=path,
            settings=settings,
        )
    entries, truncated = list_result
    payload = ListContainerDirectoryPayload(
        action="list_container_directory",
        requested_project_name=project_name,
        project_name=context.project_name,
        source_key=context.definition.source_key,
        container_name=context.definition.target,
        path=context.normalized_path,
        truncated=truncated,
        entries=[create_container_payload(entry) for entry in entries],
    )

    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "list_container_directory",
            "source_key": payload.source_key,
            "container_name": payload.container_name,
            "path": payload.path,
            "entry_count": len(payload.entries),
            "truncated": payload.truncated,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))
