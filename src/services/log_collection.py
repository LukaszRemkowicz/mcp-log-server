"""Collection orchestration service for persisted project log snapshots."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from conf import settings
from core.types import LogWorkspace
from database.schemas import (
    AgentSessionOut,
    CollectLogsCreate,
    CollectLogsOut,
    CollectLogsSourceCreate,
    CollectLogsWithSourcesOut,
)
from database.services.agent_sessions import AgentSessionService as AgentSessionDBService
from database.services.collect_logs import CollectLogsService as CollectLogsDBService
from database.services.collect_logs import CollectLogsSourceService as CollectLogsSourceDBService
from exceptions import DockerSocketGatewayError, InvalidTimeFilterError, MissingSessionIdError
from manifests.models import Manifest, SourceDefinition
from tools.models import (
    CollectedSourcePayload,
    ProjectCollectLogsPayload,
    SnapshotWorkspace,
    SourceProvenanceDiagnosticPayload,
)
from tools.utils import parse_snapshot_retention
from utils.log_snapshots import (
    COLLECTION_DIAGNOSTICS_DESCRIPTION,
    COLLECTION_DIAGNOSTICS_FILE_NAME,
    COLLECTION_DIAGNOSTICS_SOURCE_KEY,
)

from .docker_socket_gateway import DockerSocketGatewayClient
from .log_snapshots import LogSnapshotService
from .session_ids import SESSION_ID_MAX_LENGTH, generate_session_id

_DOCKER_DURATION_PATTERN = re.compile(r"(?P<value>\d+)(?P<unit>[smhd])")
_RAW_NGINX_TIMESTAMP_PATTERN = re.compile(r"\[(?P<timestamp>\d{2}/[A-Za-z]{3}/\d{4}:[^\]]+)\]")
_NGINX_ERROR_TIMESTAMP_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\s+"
)
_FAIL2BAN_TIMESTAMP_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d+)?"
)
_ISO_PREFIX_TIMESTAMP_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)"
)
DockerTimeFilter = datetime | int | None
_DOCKER_LOG_PAGE_MAX_BYTES = 1_000_000


class DockerSocketClientProtocol(Protocol):
    """Fixed-operation socket client contract used by log collection."""

    def request(self, operation: str, params: Mapping[str, object]) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class CollectionDefaults:
    """Normalized request defaults used before per-project collection starts."""

    source_keys: list[str]
    since: str


@dataclass(frozen=True, slots=True)
class DockerTimeFilters:
    """Docker-ready since/until filters normalized from collect_logs input."""

    since: DockerTimeFilter
    until: DockerTimeFilter
    file_filters_enabled: bool = False


class BuildLogsError(BaseModel):
    """Service-level failure that prevents one project artifact from being built.

    `collect_logs` tools convert this into the public MCP error contract. The
    service returns this model instead of raising for expected request problems,
    such as missing `session_id` or invalid Docker time filters.
    """

    message: str
    error_code: str
    retry_tips: list[str]


class SourceCollectionResult(BaseModel):
    """Internal per-source collection result before DB persistence.

    Collection adapters return this shape for both success and unavailable
    cases. Public MCP payloads and DB models are built later from this internal
    boundary instead of inferring source state from unrelated success/error
    model classes.
    """

    source_key: str
    source_type: Literal["docker", "file"]
    target: str
    description: str
    stream: Literal["stdout", "stderr"] | None
    parser_type: str | None
    normalization_profile: str | None
    default_noise_profile: str | None
    status: Literal["collected", "unavailable"]
    output_file: str | None = None
    line_count: int = 0
    byte_count: int = 0
    transfer: dict[str, object] | None = None
    error: str | None = None
    retry_tips: list[str]

    def __getitem__(self, key: str) -> object:
        """Allow concise dict-style assertions while keeping a typed model contract."""

        return getattr(self, key)

    @classmethod
    def collected(
        cls,
        definition: SourceDefinition,
        *,
        output_file: Path,
        line_count: int,
        byte_count: int,
        transfer: dict[str, object] | None = None,
    ) -> SourceCollectionResult:
        """Build the internal success result for one collected source."""

        return cls(
            source_key=definition.source_key,
            source_type=definition.source_type,
            target=definition.target,
            description=definition.description,
            stream=definition.stream,
            parser_type=definition.parser_type,
            normalization_profile=definition.normalization_profile,
            default_noise_profile=definition.default_noise_profile,
            status="collected",
            output_file=str(output_file),
            line_count=line_count,
            byte_count=byte_count,
            transfer=transfer,
            error=None,
            retry_tips=[],
        )

    @classmethod
    def unavailable(
        cls,
        definition: SourceDefinition,
        *,
        error: str,
        retry_tips: list[str],
    ) -> SourceCollectionResult:
        """Build the internal unavailable result for one failed source."""

        return cls(
            source_key=definition.source_key,
            source_type=definition.source_type,
            target=definition.target,
            description=definition.description,
            stream=definition.stream,
            parser_type=definition.parser_type,
            normalization_profile=definition.normalization_profile,
            default_noise_profile=definition.default_noise_profile,
            status="unavailable",
            output_file=None,
            line_count=0,
            byte_count=0,
            transfer=None,
            error=error,
            retry_tips=retry_tips,
        )


class LogCollectionService:
    """Orchestrate one complete `collect_logs` request end-to-end.

    Responsibility:

    - call the low-level source collector for each resolved source
    - delegate snapshot directory preparation and persistence to
      `LogSnapshotService`
    - assemble the agent-facing `CollectLogsPayload`

    This service owns deterministic source collection plus snapshot
    persistence. Manifest loading belongs to `ProjectManifestService` and
    snapshot lifecycle belongs to `LogSnapshotService`.
    """

    def __init__(
        self,
        *,
        collect_logs_db_service: CollectLogsDBService | None = None,
        collect_logs_source_db_service: CollectLogsSourceDBService | None = None,
        agent_session_db_service: AgentSessionDBService | None = None,
        docker_socket_client: DockerSocketClientProtocol | None = None,
    ) -> None:
        self.snapshot_service = LogSnapshotService()
        self.collect_logs_db_service = collect_logs_db_service or CollectLogsDBService()
        self.collect_logs_source_db_service = (
            collect_logs_source_db_service or CollectLogsSourceDBService()
        )
        self.agent_session_db_service = agent_session_db_service or AgentSessionDBService()
        self.docker_socket_client = docker_socket_client or DockerSocketGatewayClient()

    @staticmethod
    def normalize_params(
        source_keys: list[str] | None,
        since: str | None,
    ) -> CollectionDefaults:
        """Apply public `collect_logs` defaults before manifest source resolution."""

        return CollectionDefaults(
            source_keys=["all"] if source_keys is None else source_keys,
            since=settings.DEFAULT_LOG_WINDOW if since is None else since,
        )

    @staticmethod
    def resolve_session_id(session_id: object) -> str:
        """Return the effective collect_logs session id for the request."""

        if isinstance(session_id, str) and session_id.strip():
            normalized_session_id = session_id.strip()
            if len(normalized_session_id) > SESSION_ID_MAX_LENGTH:
                raise ValueError(f"session_id must be {SESSION_ID_MAX_LENGTH} characters or fewer.")
            return normalized_session_id
        return generate_session_id()

    async def build_logs(
        self,
        manifest: Manifest,
        sources: list[SourceDefinition],
        missing_source_keys: list[str],
        source_keys: list[str],
        workspace: SnapshotWorkspace,
        session_id: str | None,
        since: str | None,
        until: str | None,
    ) -> ProjectCollectLogsPayload | BuildLogsError:
        """Collect and persist logs for one manifest-backed project.

        The caller must already have loaded the manifest, resolved source keys,
        and authorized project access. This method owns the collection run for
        that one project:

        - prepare workflow/session snapshot directories
        - collect each selected source into a persisted file
        - write snapshot metadata or workflow inventory
        - return one project payload for the top-level `collect_logs` response

        Expected request errors are returned as `BuildLogsError`; per-source
        failures are kept inside the successful project payload.
        """

        try:
            time_filters = self.validate_and_normalize_time_filters(
                since=since,
                until=until,
            )
        except InvalidTimeFilterError as error:
            return self._build_invalid_time_filter_error(error)

        agent_session = await self.resolve_agent_session(session_id)
        if isinstance(agent_session, BuildLogsError):
            return agent_session

        async with self.snapshot_service.collection_transaction():
            if workspace == LogWorkspace.SESSION:
                return await self.build_session_logs(
                    manifest=manifest,
                    sources=sources,
                    missing_source_keys=missing_source_keys,
                    source_keys=source_keys,
                    since=since,
                    until=until,
                    time_filters=time_filters,
                    agent_session=agent_session,
                )
            if workspace == LogWorkspace.WORKFLOW:
                return await self.build_workflow_logs(
                    manifest=manifest,
                    sources=sources,
                    missing_source_keys=missing_source_keys,
                    source_keys=source_keys,
                    since=since,
                    until=until,
                    time_filters=time_filters,
                    agent_session=agent_session,
                )
            raise RuntimeError(f"Unsupported collect_logs workspace: {workspace}")

    async def resolve_agent_session(
        self,
        session_id: str | None,
    ) -> AgentSessionOut | BuildLogsError:
        """Load the DB-backed session row from the agent-facing session id."""

        if not session_id or not session_id.strip():
            return BuildLogsError(
                message=(
                    "Session is unavailable because MCP did not provide the required session_id."
                ),
                error_code="missing_session_id",
                retry_tips=[
                    "This is a system error, not something the agent can fix with tool arguments.",
                    "Ask administrator to check MCP middleware and session propagation.",
                ],
            )
        agent_session = await self.agent_session_db_service.get_by_name(session_id.strip())
        if agent_session is None:
            return BuildLogsError(
                message="Session is unavailable because its DB row was not found.",
                error_code="session_not_found",
                retry_tips=[
                    "This is a system error, not something the agent can fix with tool arguments.",
                    "Ask administrator to check MCP middleware and session persistence.",
                ],
            )
        return agent_session

    async def build_session_logs(
        self,
        *,
        manifest: Manifest,
        sources: list[SourceDefinition],
        missing_source_keys: list[str],
        source_keys: list[str],
        since: str | None,
        until: str | None,
        time_filters: DockerTimeFilters,
        agent_session: AgentSessionOut,
    ) -> ProjectCollectLogsPayload | BuildLogsError:
        """Collect one session snapshot and persist its DB metadata."""

        project_name = manifest.project_key
        warnings, retry_tips = self._build_feedback(missing_source_keys=missing_source_keys)
        try:
            snapshot_dir = self.snapshot_service.prepare_workspace(
                project_name=project_name,
                workspace=LogWorkspace.SESSION,
                session_id=agent_session.name,
                snapshot_dir=None,
            )
        except MissingSessionIdError:
            return BuildLogsError(
                message=(
                    "Session workspace is unavailable because MCP did not provide "
                    "the required session_id."
                ),
                error_code="missing_session_id",
                retry_tips=[
                    "This is a system error, not something the agent can fix with tool arguments.",
                    (
                        "Ask administrator to check MCP middleware, session propagation, "
                        "and system logs."
                    ),
                ],
            )
        collected_results = await self.collect_sources_async(
            sources=sources,
            snapshot_dir=snapshot_dir,
            time_filters=time_filters,
        )
        collected_source_keys = self._build_collected_manifest_source_keys(
            source_keys=source_keys,
            collected_results=collected_results,
        )

        requested_source_keys = [*source_keys, *missing_source_keys]
        collect_logs_obj = await self.save_logs_to_db(
            collect_logs_payload=CollectLogsCreate(
                workspace=LogWorkspace.SESSION,
                session_id=agent_session.id,
                project_name=project_name,
                collected_at=datetime.now(UTC),
                snapshot_dir=snapshot_dir.as_posix(),
                is_latest=False,
                requested_source_keys=requested_source_keys,
                resolved_source_keys=collected_source_keys,
                unknown_requested_source_keys=missing_source_keys,
                requested_since=since,
                requested_until=until,
                warnings=warnings,
                retry_tips=retry_tips,
            ),
            collected_results=collected_results,
        )
        return self._build_project_payload(collect_logs_obj)

    async def build_workflow_logs(
        self,
        *,
        manifest: Manifest,
        sources: list[SourceDefinition],
        missing_source_keys: list[str],
        source_keys: list[str],
        since: str | None,
        until: str | None,
        time_filters: DockerTimeFilters,
        agent_session: AgentSessionOut,
    ) -> ProjectCollectLogsPayload | BuildLogsError:
        """Collect the latest workflow snapshot and persist its DB metadata."""

        project_name = manifest.project_key
        warnings, retry_tips = self._build_feedback(missing_source_keys=missing_source_keys)

        workflow_collect_logs_obj = await self.create_workflow_collect_logs_obj(
            project_name=project_name,
            source_keys=source_keys,
            missing_source_keys=missing_source_keys,
            since=since,
            until=until,
            warnings=warnings,
            retry_tips=retry_tips,
            agent_session=agent_session,
        )
        snapshot_dir = self.snapshot_service.prepare_workspace(
            project_name=project_name,
            workspace=LogWorkspace.WORKFLOW,
            session_id=None,
            snapshot_dir=workflow_collect_logs_obj.snapshot_dir,
        )
        collected_results = await self.collect_sources_async(
            sources=sources,
            snapshot_dir=snapshot_dir,
            time_filters=time_filters,
        )
        collected_source_keys = self._build_collected_manifest_source_keys(
            source_keys=source_keys,
            collected_results=collected_results,
        )
        collect_logs_obj = await self.save_sources_to_db(
            collect_logs_obj=workflow_collect_logs_obj,
            collected_results=collected_results,
            resolved_source_keys=collected_source_keys,
        )
        return self._build_project_payload(collect_logs_obj)

    @staticmethod
    def _build_collected_manifest_source_keys(
        *,
        source_keys: list[str],
        collected_results: list[SourceCollectionResult],
    ) -> list[str]:
        """Return requested manifest source keys with usable persisted log snapshots."""

        collected_result_keys = {
            result.source_key
            for result in collected_results
            if result.status == "collected" and result.output_file
        }
        return [source_key for source_key in source_keys if source_key in collected_result_keys]

    def collect_sources(
        self,
        *,
        sources: list[SourceDefinition],
        snapshot_dir: Path,
        time_filters: DockerTimeFilters,
    ) -> list[SourceCollectionResult]:
        """Collect all resolved sources into the prepared snapshot directory."""

        collected_results: list[SourceCollectionResult] = []
        for source in sources:
            collected_results.append(
                self.collect_source(
                    source,
                    output_file=snapshot_dir / f"{source.source_key}.log",
                    time_filters=time_filters,
                )
            )
        diagnostics_result = self._build_collection_diagnostics_result(
            collected_results,
            snapshot_dir=snapshot_dir,
        )
        if diagnostics_result is not None:
            collected_results.append(diagnostics_result)
        return collected_results

    async def collect_sources_async(
        self,
        *,
        sources: list[SourceDefinition],
        snapshot_dir: Path,
        time_filters: DockerTimeFilters,
    ) -> list[SourceCollectionResult]:
        """Collect in a worker while keeping cancellation inside the snapshot lock."""

        worker_task = asyncio.create_task(
            asyncio.to_thread(
                self.collect_sources,
                sources=sources,
                snapshot_dir=snapshot_dir,
                time_filters=time_filters,
            )
        )
        try:
            return await asyncio.shield(worker_task)
        except asyncio.CancelledError:
            # A thread cannot be cancelled. Wait for it before allowing the caller's
            # collection transaction to release its cross-process snapshot lock.
            await worker_task
            raise

    @staticmethod
    def _build_collection_diagnostics_result(
        collected_results: list[SourceCollectionResult],
        *,
        snapshot_dir: Path,
    ) -> SourceCollectionResult | None:
        """Persist a sidecar diagnostics artifact for failed source collections."""

        failed_results = [result for result in collected_results if result.status == "unavailable"]
        if not failed_results:
            return None

        output_file = snapshot_dir / COLLECTION_DIAGNOSTICS_FILE_NAME
        diagnostics_payload = {
            "artifact_type": "collection_diagnostics",
            "failed_source_count": len(failed_results),
            "failed_sources": [
                {
                    "source_key": result.source_key,
                    "source_type": result.source_type,
                    "target": result.target,
                    "description": result.description,
                    "stream": result.stream,
                    "status": result.status,
                    "error": result.error,
                    "retry_tips": result.retry_tips,
                    "provenance": LogCollectionService._build_source_provenance_diagnostic(
                        source_key=result.source_key,
                        source_type=result.source_type,
                    ).model_dump(mode="json"),
                }
                for result in failed_results
            ],
        }
        content = json.dumps(diagnostics_payload, indent=2, sort_keys=True) + "\n"
        output_file.write_text(content, encoding="utf-8")
        byte_count = len(content.encode("utf-8"))
        line_count = content.count("\n")
        return SourceCollectionResult(
            source_key=COLLECTION_DIAGNOSTICS_SOURCE_KEY,
            source_type="file",
            target=COLLECTION_DIAGNOSTICS_FILE_NAME,
            description=COLLECTION_DIAGNOSTICS_DESCRIPTION,
            stream=None,
            parser_type="collection_diagnostics",
            normalization_profile=None,
            default_noise_profile=None,
            status="collected",
            output_file=str(output_file),
            line_count=line_count,
            byte_count=byte_count,
            transfer=None,
            error=None,
            retry_tips=[],
        )

    @staticmethod
    def _build_feedback(
        missing_source_keys: list[str],
    ) -> tuple[list[str], list[str]]:
        """Build deterministic warnings and retry tips for one collection request."""

        warnings: list[str] = []
        retry_tips: list[str] = []

        if missing_source_keys:
            warnings.append(
                "Some requested source_keys were not found in the configured manifest: "
                + ", ".join(missing_source_keys)
                + "."
            )
            retry_tips.append(
                "Retry with only source_keys returned by the manifest-backed project configuration."
            )

        return warnings, retry_tips

    @staticmethod
    def _build_source_provenance_diagnostic(
        *,
        source_key: str,
        source_type: Literal["docker", "file"],
    ) -> SourceProvenanceDiagnosticPayload:
        """Return source-type-specific provenance follow-up guidance."""

        recommended_tools = ["explain_project_source"]
        if source_type == "file":
            recommended_tools.extend(
                [
                    "stat_project_path",
                    "list_project_directory",
                    "inspect_project_scheduled_jobs",
                ]
            )
        else:
            recommended_tools.extend(
                [
                    "inspect_containers_health",
                    "inspect_project_compose_state",
                    "inspect_project_runtime",
                    "inspect_project_deployment",
                ]
            )
        return SourceProvenanceDiagnosticPayload(
            source_key=source_key,
            source_type=source_type,
            status="unavailable",
            summary=(
                "Configured source was unavailable during collection; use "
                "project-scoped provenance tools before interpreting this as "
                "healthy or empty logs."
            ),
            recommended_tools=recommended_tools,
        )

    async def save_logs_to_db(
        self,
        *,
        collect_logs_payload: CollectLogsCreate,
        collected_results: list[SourceCollectionResult],
    ) -> CollectLogsWithSourcesOut:
        """Save CollectLogs and CollectLogsSource rows for the written snapshot files."""

        collect_logs_obj = await self.collect_logs_db_service.create(collect_logs_payload)
        return await self.save_sources_to_db(
            collect_logs_obj=collect_logs_obj,
            collected_results=collected_results,
        )

    async def save_sources_to_db(
        self,
        *,
        collect_logs_obj: CollectLogsOut,
        collected_results: list[SourceCollectionResult],
        resolved_source_keys: list[str] | None = None,
    ) -> CollectLogsWithSourcesOut:
        """Save CollectLogsSource objects for one existing CollectLogs artifact object."""

        await self.collect_logs_source_db_service.create_many(
            collect_logs_obj,
            [
                self._build_source_create_payload(
                    result=result,
                )
                for result in collected_results
            ],
        )
        if resolved_source_keys is not None:
            await self.collect_logs_db_service.update_resolved_source_keys(
                collect_logs_obj.id,
                resolved_source_keys,
            )
        return await self.collect_logs_db_service.get_with_sources(collect_logs_obj.id)

    async def create_workflow_collect_logs_obj(
        self,
        *,
        project_name: str,
        source_keys: list[str],
        missing_source_keys: list[str],
        since: str | None,
        until: str | None,
        warnings: list[str],
        retry_tips: list[str],
        agent_session: AgentSessionOut,
    ) -> CollectLogsOut:
        """Archive previous latest and create the DB object that owns workflow snapshot_dir."""

        await self.archive_latest_for_project(project_name)
        await self.prune_workflow_archives_for_project(project_name)
        return await self.collect_logs_db_service.create(
            CollectLogsCreate(
                workspace=LogWorkspace.WORKFLOW,
                session_id=agent_session.id,
                project_name=project_name,
                collected_at=datetime.now(UTC),
                snapshot_dir=self.snapshot_service.storage.workflow_latest_dir(
                    project_name
                ).as_posix(),
                is_latest=True,
                requested_source_keys=[*source_keys, *missing_source_keys],
                resolved_source_keys=[],
                unknown_requested_source_keys=missing_source_keys,
                requested_since=since,
                requested_until=until,
                warnings=warnings,
                retry_tips=retry_tips,
            )
        )

    @staticmethod
    def validate_and_normalize_time_filters(
        *,
        since: str | None,
        until: str | None,
    ) -> DockerTimeFilters:
        """Return normalized time filters for docker and file source collectors."""

        return DockerTimeFilters(
            since=LogCollectionService.normalize_docker_time_filter(since),
            until=LogCollectionService.normalize_docker_time_filter(until),
            file_filters_enabled=(
                until is not None
                or (since is not None and since.strip() != settings.DEFAULT_LOG_WINDOW)
            ),
        )

    @staticmethod
    def _build_invalid_time_filter_error(error: InvalidTimeFilterError) -> BuildLogsError:
        """Return the public invalid time filter error."""

        return BuildLogsError(
            message=str(error),
            error_code="invalid_docker_time_filter",
            retry_tips=[
                (
                    "Retry with since/until as ISO-8601, unix seconds, or a "
                    "duration like 30m, 1h, or 1d."
                ),
                "Omit since/until if you want the current default collection range.",
            ],
        )

    async def archive_latest_for_project(self, project_name: str) -> None:
        """Archive the current latest workflow snapshot for one project."""

        obj = await self.collect_logs_db_service.get_latest_with_sources(project_name)
        if obj is None:
            return

        archive_name = obj.collected_at.astimezone(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
        archive_snapshot_dir = self._archived_snapshot_dir(obj.snapshot_dir, archive_name)
        current_snapshot_dir = self._resolve_storage_path(obj.snapshot_dir)
        archive_path = self._resolve_storage_path(archive_snapshot_dir)
        if archive_path.exists():
            shutil.rmtree(archive_path)
        if current_snapshot_dir.exists():
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(current_snapshot_dir), archive_path)
        for source in obj.sources:
            if source.file is None:
                continue
            await self.collect_logs_source_db_service.update_file(
                source.id,
                self._archived_source_file(source.file.name, archive_name),
            )
        await self.collect_logs_db_service.archive(
            obj.id,
            archive_name=archive_name,
            snapshot_dir=archive_snapshot_dir,
        )

    async def prune_workflow_archives_for_project(self, project_name: str) -> None:
        """Prune expired workflow archives together with their DB metadata."""

        archive_root = self.snapshot_service.storage.workflow_archive_dir(project_name)
        if not archive_root.exists():
            return

        cutoff = datetime.now(UTC) - parse_snapshot_retention(settings.WORKFLOW_ARCHIVE_RETENTION)
        archived_rows = await self.collect_logs_db_service.list_workflow_archives(project_name)
        rows_by_archive_name = {
            row.archive_name: row for row in archived_rows if row.archive_name is not None
        }
        for archive_dir in archive_root.iterdir():
            if not archive_dir.is_dir():
                continue
            archive_modified_at = datetime.fromtimestamp(archive_dir.stat().st_mtime, UTC)
            if archive_modified_at >= cutoff:
                continue
            shutil.rmtree(archive_dir)
            archived_row = rows_by_archive_name.get(archive_dir.name)
            if archived_row is not None:
                await self.collect_logs_db_service.delete(archived_row.id)

    @staticmethod
    def _archived_snapshot_dir(snapshot_dir: str, archive_name: str) -> str:
        """Return snapshot dir rewritten from workflow latest to archive path."""

        path = Path(snapshot_dir)
        if path.name != "latest":
            return snapshot_dir
        return (path.parent / "archive" / archive_name).as_posix()

    @staticmethod
    def _archived_source_file(file_name: str, archive_name: str) -> str:
        """Return source file path rewritten from workflow latest to archive path."""

        path = Path(file_name)
        parts = list(path.parts)
        if "latest" not in parts:
            return file_name
        latest_index = parts.index("latest")
        parts[latest_index : latest_index + 1] = ["archive", archive_name]
        return Path(*parts).as_posix()

    def _resolve_storage_path(self, path: str) -> Path:
        """Resolve an absolute or logs-root-relative snapshot path."""

        snapshot_path = Path(path)
        if snapshot_path.is_absolute():
            return snapshot_path
        return self.snapshot_service.storage.path(snapshot_path)

    def _build_source_create_payload(
        self,
        *,
        result: SourceCollectionResult,
    ) -> CollectLogsSourceCreate:
        """Build one DB source payload from a collection result."""

        if result.status == "unavailable":
            return CollectLogsSourceCreate(
                source_key=result.source_key,
                source_type=result.source_type,
                target=result.target,
                description=result.description,
                stream=result.stream,
                parser_type=result.parser_type,
                normalization_profile=result.normalization_profile,
                default_noise_profile=result.default_noise_profile,
                status="unavailable",
                file=None,
                line_count=0,
                transfer=None,
                error=result.error,
                retry_tips=result.retry_tips,
            )

        assert result.output_file is not None
        file_path = self.snapshot_service.storage.relative_name(Path(result.output_file))
        return CollectLogsSourceCreate(
            source_key=result.source_key,
            source_type=result.source_type,
            target=result.target,
            description=result.description,
            stream=result.stream,
            parser_type=result.parser_type,
            normalization_profile=result.normalization_profile,
            default_noise_profile=result.default_noise_profile,
            status="collected",
            file=file_path,
            line_count=result.line_count,
            transfer=result.transfer if result.source_type == "docker" else None,
            error=None,
            retry_tips=[],
        )

    def collect_source(
        self,
        definition: SourceDefinition,
        output_file: Path,
        time_filters: DockerTimeFilters,
    ) -> SourceCollectionResult:
        """Collect one manifest source through its deterministic adapter.

        File sources are copied directly. Docker sources are streamed from the
        Docker Engine API into `output_file`. The return value is either the
        persisted file metadata or a source-level error that can be reported
        without aborting the rest of the collection.
        """

        if definition.source_type == "file":
            return self._collect_file_source(
                definition,
                output_file=output_file,
                time_filters=time_filters,
            )
        return self._collect_docker_source(
            definition,
            output_file=output_file,
            time_filters=time_filters,
        )

    @classmethod
    def _write_file_to_output(
        cls,
        path: Path,
        output_file: Path,
        *,
        time_filters: DockerTimeFilters,
    ) -> tuple[int, int]:
        """Copy one file-backed source into the destination log file.

        File sources cannot use Docker Engine time filtering, so collection
        applies the same since/until window while streaming lines. Timestamped
        lines are filtered directly; untimestamped continuation lines are kept
        only after the previous timestamped line was included.
        """

        if not time_filters.file_filters_enabled:
            return cls._copy_file_to_output(path, output_file)

        byte_count = 0
        newline_count = 0
        trailing_byte: bytes = b""
        previous_timestamp_included = time_filters.since is None and time_filters.until is None
        with path.open("rb") as source_handle, output_file.open("wb") as output_handle:
            for line in source_handle:
                decoded_line = line.decode("utf-8", errors="replace")
                line_timestamp = cls.parse_log_line_timestamp(decoded_line)
                if line_timestamp is None:
                    if not previous_timestamp_included:
                        continue
                else:
                    previous_timestamp_included = cls.timestamp_in_window(
                        line_timestamp,
                        time_filters=time_filters,
                    )
                    if not previous_timestamp_included:
                        continue
                output_handle.write(line)
                byte_count += len(line)
                newline_count += line.count(b"\n")
                trailing_byte = line[-1:]
        if byte_count == 0:
            return 0, 0
        if trailing_byte == b"\n":
            return byte_count, newline_count
        return byte_count, newline_count + 1

    @staticmethod
    def _copy_file_to_output(path: Path, output_file: Path) -> tuple[int, int]:
        """Copy one file-backed source directly into the destination log file."""

        byte_count = 0
        newline_count = 0
        trailing_byte: bytes = b""
        with path.open("rb") as source_handle, output_file.open("wb") as output_handle:
            for chunk in iter(lambda: source_handle.read(8192), b""):
                output_handle.write(chunk)
                byte_count += len(chunk)
                newline_count += chunk.count(b"\n")
                if chunk:
                    trailing_byte = chunk[-1:]
        if byte_count == 0:
            return 0, 0
        if trailing_byte == b"\n":
            return byte_count, newline_count
        return byte_count, newline_count + 1

    @staticmethod
    def _time_filter_to_datetime(value: DockerTimeFilter) -> datetime | None:
        """Return one normalized UTC datetime from a time filter value."""

        if value is None:
            return None
        if isinstance(value, int):
            return datetime.fromtimestamp(value, UTC)
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @classmethod
    def timestamp_in_window(
        cls,
        timestamp: datetime,
        *,
        time_filters: DockerTimeFilters,
    ) -> bool:
        """Return whether a timestamp falls inside the requested collection window."""

        normalized_timestamp = timestamp.astimezone(UTC)
        since = cls._time_filter_to_datetime(time_filters.since)
        until = cls._time_filter_to_datetime(time_filters.until)
        if since is not None and normalized_timestamp < since:
            return False
        if until is not None and normalized_timestamp > until:
            return False
        return True

    @classmethod
    def parse_log_line_timestamp(cls, line: str) -> datetime | None:
        """Parse supported timestamp formats from one log line."""

        stripped_line = line.strip()
        if not stripped_line:
            return None

        json_timestamp = cls._parse_json_log_timestamp(stripped_line)
        if json_timestamp is not None:
            return json_timestamp

        raw_nginx_match = _RAW_NGINX_TIMESTAMP_PATTERN.search(stripped_line)
        if raw_nginx_match is not None:
            return datetime.strptime(
                raw_nginx_match.group("timestamp"),
                "%d/%b/%Y:%H:%M:%S %z",
            ).astimezone(UTC)

        nginx_error_match = _NGINX_ERROR_TIMESTAMP_PATTERN.match(stripped_line)
        if nginx_error_match is not None:
            return datetime.strptime(
                nginx_error_match.group("timestamp"),
                "%Y/%m/%d %H:%M:%S",
            ).replace(tzinfo=UTC)

        fail2ban_match = _FAIL2BAN_TIMESTAMP_PATTERN.match(stripped_line)
        if fail2ban_match is not None:
            return datetime.strptime(
                fail2ban_match.group("timestamp"),
                "%Y-%m-%d %H:%M:%S",
            ).replace(tzinfo=UTC)

        iso_match = _ISO_PREFIX_TIMESTAMP_PATTERN.match(stripped_line)
        if iso_match is not None:
            return cls._parse_iso_timestamp(iso_match.group("timestamp"))

        return None

    @classmethod
    def _parse_json_log_timestamp(cls, line: str) -> datetime | None:
        """Return a timestamp from common structured JSON log fields."""

        if not line.startswith("{"):
            return None
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        timestamp_value = (
            payload.get("timestamp") or payload.get("time") or payload.get("time_local")
        )
        if not isinstance(timestamp_value, str) or not timestamp_value.strip():
            return None
        stripped_timestamp = timestamp_value.strip()
        try:
            if "/" in stripped_timestamp and ":" in stripped_timestamp:
                return datetime.strptime(
                    stripped_timestamp,
                    "%d/%b/%Y:%H:%M:%S %z",
                ).astimezone(UTC)
            return cls._parse_iso_timestamp(stripped_timestamp)
        except ValueError:
            return None

    @staticmethod
    def _parse_iso_timestamp(value: str) -> datetime:
        """Parse an ISO-like timestamp and normalize it to UTC."""

        parsed_datetime = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed_datetime.tzinfo is None:
            parsed_datetime = parsed_datetime.replace(tzinfo=UTC)
        return parsed_datetime.astimezone(UTC)

    @staticmethod
    def normalize_docker_time_filter(value: str | None) -> datetime | int | None:
        """Normalize an agent-facing time filter into a Docker SDK value.

        Accepted values:

        - `None` or blank strings, meaning no filter
        - unix seconds as digits
        - relative durations such as `30m`, `1h`, or `1d`
        - ISO-8601 timestamps, with optional trailing `Z`

        Raises:
            ValueError: When the value cannot be parsed into a Docker-compatible
                time filter.
        """

        if value is None:
            return None

        stripped_value = value.strip()
        if not stripped_value:
            return None

        if stripped_value.isdigit():
            return int(stripped_value)

        duration_match = _DOCKER_DURATION_PATTERN.fullmatch(stripped_value)
        if duration_match is not None:
            duration_value = int(duration_match.group("value"))
            duration_unit = duration_match.group("unit")
            duration_kwargs = {
                "s": {"seconds": duration_value},
                "m": {"minutes": duration_value},
                "h": {"hours": duration_value},
                "d": {"days": duration_value},
            }
            return datetime.now(UTC) - timedelta(**duration_kwargs[duration_unit])

        normalized_iso_value = stripped_value.replace("Z", "+00:00")
        try:
            parsed_datetime = datetime.fromisoformat(normalized_iso_value)
        except ValueError as error:
            raise InvalidTimeFilterError(
                f"Invalid docker time filter {value!r}. Use an ISO-8601 timestamp, "
                "unix seconds, or a duration like 30m, 1h, or 1d."
            ) from error

        if parsed_datetime.tzinfo is None:
            return parsed_datetime.replace(tzinfo=UTC)
        return parsed_datetime

    def _collect_file_source(
        self,
        definition: SourceDefinition,
        output_file: Path,
        time_filters: DockerTimeFilters,
    ) -> SourceCollectionResult:
        """Collect one file-backed source declared by the manifest.

        File-backed sources must point at an explicit absolute path. Different
        sources can live in different places, so the manifest owns that path.
        """

        target_path = Path(definition.target)
        if not target_path.is_absolute():
            return SourceCollectionResult.unavailable(
                definition,
                error="File source target must be an absolute path.",
                retry_tips=[
                    "Use an explicit absolute path in the persisted manifest source target."
                ],
            )
        path = target_path
        if not path.exists():
            return SourceCollectionResult.unavailable(
                definition,
                error=f"File source not found: {definition.target}",
                retry_tips=[
                    "Verify the file path in the manifest or retry with a different source."
                ],
            )

        byte_count, line_count = self._write_file_to_output(
            path,
            output_file,
            time_filters=time_filters,
        )
        return SourceCollectionResult.collected(
            definition,
            output_file=output_file,
            line_count=line_count,
            byte_count=byte_count,
        )

    def _collect_docker_source(
        self,
        definition: SourceDefinition,
        output_file: Path,
        time_filters: DockerTimeFilters,
    ) -> SourceCollectionResult:
        """Collect one docker-backed source through the Docker Engine API."""

        logs_kwargs: dict[str, int | str | datetime] = {}
        if time_filters.since is not None:
            logs_kwargs["since"] = time_filters.since
        # Paging replays the bounded Docker logs query for every offset. Freeze
        # an upper bound once so a growing live stream cannot shift between pages.
        logs_kwargs["until"] = time_filters.until or datetime.now(UTC)

        stream_target = self._resolve_docker_log_container(definition)
        if isinstance(stream_target, SourceCollectionResult):
            return stream_target
        try:
            byte_count = 0
            newline_count = 0
            trailing_byte: bytes = b""
            page_count = 0
            final_page: dict[str, object] | None = None
            with output_file.open("wb") as handle:
                for chunk, page_metadata in self._stream_docker_log_pages(
                    container_name=stream_target,
                    stream=definition.stream,
                    logs_kwargs=logs_kwargs,
                ):
                    handle.write(chunk)
                    byte_count += len(chunk)
                    newline_count += chunk.count(b"\n")
                    if chunk:
                        trailing_byte = chunk[-1:]
                    page_count += 1
                    final_page = page_metadata
            persisted_output_file = str(output_file)
            if byte_count == 0:
                line_count = 0
            elif trailing_byte == b"\n":
                line_count = newline_count
            else:
                line_count = newline_count + 1
        except DockerSocketGatewayError as error:
            if error.error_code == "docker_log_timeout":
                error_message = error.message
                error_message += (
                    " Retry with a narrower since/until window to limit the requested log output."
                )
                return self._build_docker_timeout_error(definition, error_message=error_message)
            if error.error_code == "docker_engine_unavailable":
                return self._build_docker_unavailable_error(definition)
            return self._build_docker_source_error(definition, error=error.message)
        return SourceCollectionResult.collected(
            definition,
            output_file=Path(persisted_output_file),
            line_count=line_count,
            byte_count=byte_count,
            transfer={
                "operation": "container_logs_page",
                "encoding": "base64",
                "page_count": page_count,
                "returned_bytes": byte_count,
                "byte_limit": final_page["byte_limit"] if final_page is not None else 0,
                "truncated": final_page["truncated"] if final_page is not None else False,
                "next_offset": final_page["next_offset"] if final_page is not None else None,
            },
        )

    def _resolve_docker_log_container(
        self,
        definition: SourceDefinition,
    ) -> str | SourceCollectionResult:
        """Resolve the running container for one manifest Compose service selector."""

        try:
            selector = f"{definition.compose_project}/{definition.compose_service}"
            resolved_container = self._resolve_container_by_compose_service(
                compose_project=str(definition.compose_project),
                compose_service=str(definition.compose_service),
            )
            if resolved_container is None:
                return self._build_docker_source_error(
                    definition,
                    error=(f"Compose service {selector!r} is not running in the current runtime."),
                )
            return resolved_container
        except DockerSocketGatewayError as error:
            if error.error_code == "docker_engine_unavailable":
                return self._build_docker_unavailable_error(definition)
            return self._build_docker_source_error(definition, error=error.message)

    def _resolve_container_by_compose_service(
        self,
        *,
        compose_project: str,
        compose_service: str,
    ) -> str | None:
        """Return the newest running non-one-off container for one Compose service."""

        payload = self.docker_socket_client.request("vps_containers_inventory", {})
        containers = payload.get("containers", [])
        if not isinstance(containers, list):
            raise DockerSocketGatewayError(message="Socket app returned invalid inventory.")
        exact_matches = [
            container
            for container in containers
            if isinstance(container, dict)
            and container.get("running") is True
            and self._compose_label(container, "com.docker.compose.project") == compose_project
            and self._compose_label(container, "com.docker.compose.service") == compose_service
            and self._compose_label(container, "com.docker.compose.oneoff").lower() != "true"
        ]
        if not exact_matches:
            return None
        newest = max(exact_matches, key=self._inventory_created_at)
        return str(newest.get("container_name") or "")

    @staticmethod
    def _compose_label(container: dict[str, object], label: str) -> str:
        """Return one normalized Compose label value from a container inventory item."""

        compose_labels = container.get("compose_labels")
        if not isinstance(compose_labels, dict):
            return ""
        value = compose_labels.get(label)
        return str(value) if value is not None else ""

    def _stream_docker_log_pages(
        self,
        *,
        container_name: str,
        stream: Literal["stdout", "stderr"] | None,
        logs_kwargs: dict[str, int | str | datetime],
    ):
        """Yield validated, lossless byte pages from the socket app."""

        params: dict[str, object] = {
            "container_name": container_name,
            "stream": stream,
        }
        for key in ("since", "until", "tail"):
            value = logs_kwargs.get(key)
            if isinstance(value, datetime):
                params[key] = value.isoformat()
            elif isinstance(value, (int, str)):
                params[key] = value
        offset = 0
        transfer_id: str | None = None
        while True:
            if transfer_id is None:
                page_params = {
                    **params,
                    "offset": offset,
                    "max_bytes": _DOCKER_LOG_PAGE_MAX_BYTES,
                }
            else:
                page_params = {
                    "transfer_id": transfer_id,
                    "offset": offset,
                    "max_bytes": _DOCKER_LOG_PAGE_MAX_BYTES,
                }
            payload = self.docker_socket_client.request("container_logs_page", page_params)
            page = self._validated_docker_log_page(payload, expected_offset=offset)
            yield page["content"], page
            if not page["truncated"]:
                return
            next_offset = page["next_offset"]
            next_transfer_id = page["transfer_id"]
            assert isinstance(next_offset, int)
            assert isinstance(next_transfer_id, str)
            transfer_id = next_transfer_id
            offset = next_offset

    @staticmethod
    def _validated_docker_log_page(
        payload: dict[str, Any],
        *,
        expected_offset: int,
    ) -> dict[str, object]:
        """Validate one page contract and decode its exact base64 bytes."""

        encoded = payload.get("logs_base64")
        try:
            content = base64.b64decode(encoded, validate=True) if isinstance(encoded, str) else None
        except (binascii.Error, ValueError) as error:
            raise DockerSocketGatewayError(
                message="Socket app returned invalid base64 docker log bytes."
            ) from error
        returned_bytes = payload.get("returned_bytes")
        byte_limit = payload.get("byte_limit")
        truncated = payload.get("truncated")
        next_offset = payload.get("next_offset")
        transfer_id = payload.get("transfer_id")
        if (
            content is None
            or payload.get("offset") != expected_offset
            or not isinstance(returned_bytes, int)
            or isinstance(returned_bytes, bool)
            or returned_bytes != len(content)
            or not isinstance(byte_limit, int)
            or isinstance(byte_limit, bool)
            or byte_limit < 1
            or not isinstance(truncated, bool)
        ):
            raise DockerSocketGatewayError(message="Socket app returned invalid docker log page.")
        expected_next_offset = expected_offset + returned_bytes
        if truncated:
            if (
                next_offset != expected_next_offset
                or returned_bytes == 0
                or not isinstance(transfer_id, str)
                or not transfer_id
            ):
                raise DockerSocketGatewayError(
                    message="Socket app returned a non-progressing docker log page."
                )
        elif next_offset is not None or transfer_id is not None:
            raise DockerSocketGatewayError(message="Socket app returned invalid final log page.")
        return {
            "content": content,
            "returned_bytes": returned_bytes,
            "byte_limit": byte_limit,
            "truncated": truncated,
            "next_offset": next_offset,
            "transfer_id": transfer_id,
        }

    @staticmethod
    def _inventory_created_at(container: dict[str, Any]) -> datetime:
        raw_created = str(container.get("created_at") or "").strip()
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
    def _build_docker_source_error(
        definition: SourceDefinition,
        *,
        error: str,
    ) -> SourceCollectionResult:
        """Return a source-level Docker collection error with stable retry tips."""

        return SourceCollectionResult.unavailable(
            definition,
            error=error,
            retry_tips=[
                "Verify the container name in the manifest or retry with a different source."
            ],
        )

    @staticmethod
    def _build_docker_timeout_error(
        definition: SourceDefinition,
        *,
        error_message: str,
    ) -> SourceCollectionResult:
        """Return a source-level Docker timeout error."""

        return SourceCollectionResult.unavailable(
            definition,
            error=error_message,
            retry_tips=[
                "Retry with a narrower since/until window to keep docker log output bounded."
            ],
        )

    @staticmethod
    def _build_docker_unavailable_error(definition: SourceDefinition) -> SourceCollectionResult:
        """Return a source-level Docker Engine unavailable error."""

        return SourceCollectionResult.unavailable(
            definition,
            error="Docker Engine API is not available in the current runtime.",
            retry_tips=["Retry in a runtime where the Docker socket is mounted and reachable."],
        )

    @staticmethod
    def _build_project_payload(
        collect_logs: CollectLogsWithSourcesOut,
    ) -> ProjectCollectLogsPayload:
        """Build the public per-project collect_logs summary from DB output."""

        source_payload_fields = {
            "source_key",
            "source_type",
            "target",
            "description",
            "stream",
            "status",
            "line_count",
            "byte_count",
            "output_file",
            "transfer",
            "error",
            "retry_tips",
        }

        return ProjectCollectLogsPayload(
            requested_project_name=collect_logs.project_name,
            project_name=collect_logs.project_name,
            workspace=collect_logs.workspace,
            snapshot_dir=collect_logs.snapshot_dir,
            requested_source_keys=collect_logs.requested_source_keys,
            requested_since=collect_logs.requested_since,
            requested_until=collect_logs.requested_until,
            warnings=collect_logs.warnings,
            retry_tips=collect_logs.retry_tips,
            unknown_requested_source_keys=collect_logs.unknown_requested_source_keys,
            resolved_source_keys=collect_logs.resolved_source_keys,
            provenance_diagnostics=[
                LogCollectionService._build_source_provenance_diagnostic(
                    source_key=source.source_key,
                    source_type=source.source_type,
                )
                for source in collect_logs.sources
                if source.status == "unavailable"
            ],
            collected_at=collect_logs.collected_at.isoformat(),
            sources=[
                CollectedSourcePayload(**source.model_dump(include=source_payload_fields))
                for source in collect_logs.sources
            ],
        )
