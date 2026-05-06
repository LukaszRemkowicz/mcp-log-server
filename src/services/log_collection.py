"""Collection orchestration service for persisted project log snapshots."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from docker.errors import APIError, DockerException
from pydantic import BaseModel
from requests import exceptions as requests_exceptions

import docker
from conf import settings
from manifests.models import SourceDefinition, SourceManifest
from tools.agent_hints import COLLECT_LOGS_NEXT_STEP_TIPS
from tools.models import (
    CollectedSourcePayload,
    LogSnapshotFilePayload,
    LogSnapshotMetadata,
    ProjectCollectLogsPayload,
    SnapshotWorkspace,
)

from .log_snapshots import LogSnapshotService

if TYPE_CHECKING:
    from docker.client import DockerClient  # type: ignore[import-not-found]

DOCKER_LOG_TIMEOUT_SECONDS = 15
_DOCKER_DURATION_PATTERN = re.compile(r"(?P<value>\d+)(?P<unit>[smhd])")


@dataclass(frozen=True, slots=True)
class CollectionDefaults:
    """Normalized request defaults used before per-project collection starts."""

    source_keys: list[str]
    since: str


class BuildLogsError(BaseModel):
    """Service-level failure that prevents one project artifact from being built.

    `collect_logs` tools convert this into the public MCP error contract. The
    service returns this model instead of raising for expected request problems,
    such as missing `session_id` or invalid Docker time filters.
    """

    message: str
    error_code: str
    retry_tips: list[str]


class CollectSourceError(BaseModel):
    """Collection failure for one manifest source inside an otherwise valid run.

    A source failure does not stop the whole project artifact. Successful
    sources are still persisted, while this model is converted into an
    `unavailable` source entry in the final response.
    """

    source_key: str
    source_type: Literal["docker", "file"]
    target: str
    description: str
    stream: Literal["stdout", "stderr"] | None
    parser_type: str | None
    normalization_profile: str | None
    default_noise_profile: str | None
    error: str
    retry_tips: list[str]

    def __getitem__(self, key: str) -> object:
        """Allow concise dict-style assertions while keeping a typed model contract."""

        return getattr(self, key)


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

    def __init__(self) -> None:
        self.snapshot_service = LogSnapshotService()

    @staticmethod
    def normalize_params(
        *,
        source_keys: list[str] | None,
        since: str | None,
    ) -> CollectionDefaults:
        """Apply public `collect_logs` defaults before manifest source resolution."""

        return CollectionDefaults(
            source_keys=["all"] if source_keys is None else source_keys,
            since=settings.DEFAULT_LOG_WINDOW if since is None else since,
        )

    def build_logs(
        self,
        *,
        manifest: SourceManifest,
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

        project_name: str = manifest.project_key
        warnings: list[str]
        retry_tips: list[str]
        warnings, retry_tips = self._build_feedback(missing_source_keys=missing_source_keys)
        normalized_session_id: str | None = session_id.strip() if session_id is not None else None
        try:
            snapshot_dir = self.snapshot_service.prepare_workspace(
                project_name=project_name,
                workspace=workspace,
                session_id=normalized_session_id,
            )
        except ValueError as error:
            return BuildLogsError(
                message=str(error),
                error_code="missing_session_id",
                retry_tips=[
                    "Retry with session_id set when workspace='session'.",
                    (
                        "Reuse the same session_id for later collect_logs calls "
                        "in the same agent session."
                    ),
                ],
            )
        try:
            collected_results: list[LogSnapshotFilePayload | CollectSourceError] = []
            for source in sources:
                collected_results.append(
                    self.collect_source(
                        source,
                        output_file=snapshot_dir / f"{source.source_key}.log",
                        since=since,
                        until=until,
                    )
                )
        except ValueError as error:
            return BuildLogsError(
                message=str(error),
                error_code="invalid_time_filter",
                retry_tips=[
                    (
                        "Retry with since/until as an ISO-8601 timestamp, "
                        "unix seconds, or a duration like 30m, 1h, or 1d."
                    )
                ],
            )
        collected_files = [
            item for item in collected_results if isinstance(item, LogSnapshotFilePayload)
        ]
        snapshot_context = self.snapshot_service.write_metadata_files(
            snapshot_dir,
            project_name=project_name,
            workspace=workspace,
            session_id=normalized_session_id,
            collected_files=collected_files,
        )
        return self._build_response(
            project_name=project_name,
            workspace=workspace,
            session_id=normalized_session_id,
            snapshot_dir=str(snapshot_context.snapshot_dir),
            metadata_file=str(snapshot_context.metadata_file),
            requested_since=since,
            requested_until=until,
            warnings=warnings,
            retry_tips=retry_tips,
            missing_source_keys=missing_source_keys,
            source_keys=source_keys,
            collected_at=snapshot_context.metadata.collected_at,
            metadata=snapshot_context.metadata,
            collected_results=collected_results,
        )

    @staticmethod
    def _build_feedback(
        *,
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

    def collect_source(
        self,
        definition: SourceDefinition,
        *,
        output_file: Path,
        since: str | None,
        until: str | None,
    ) -> LogSnapshotFilePayload | CollectSourceError:
        """Collect one manifest source through its deterministic adapter.

        File sources are copied directly. Docker sources are streamed from the
        Docker Engine API into `output_file`. The return value is either the
        persisted file metadata or a source-level error that can be reported
        without aborting the rest of the collection.
        """

        if definition.source_type == "file":
            return self._collect_file_source(definition, output_file=output_file)
        return self._collect_docker_source(
            definition,
            output_file=output_file,
            since=since,
            until=until,
        )

    @staticmethod
    def _write_file_to_output(path: Path, output_file: Path) -> tuple[int, int]:
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
            raise ValueError(
                f"Invalid docker time filter {value!r}. Use an ISO-8601 timestamp, "
                "unix seconds, or a duration like 30m, 1h, or 1d."
            ) from error

        if parsed_datetime.tzinfo is None:
            return parsed_datetime.replace(tzinfo=UTC)
        return parsed_datetime

    def _collect_file_source(
        self,
        definition: SourceDefinition,
        *,
        output_file: Path,
    ) -> LogSnapshotFilePayload | CollectSourceError:
        """Collect one file-backed source from the path declared in the manifest."""

        path = Path(definition.target)
        if not path.exists():
            return CollectSourceError(
                source_key=definition.source_key,
                source_type=definition.source_type,
                target=definition.target,
                description=definition.description,
                stream=definition.stream,
                parser_type=definition.parser_type,
                normalization_profile=definition.normalization_profile,
                default_noise_profile=definition.default_noise_profile,
                error=f"File source not found: {definition.target}",
                retry_tips=[
                    "Verify the file path in the manifest or retry with a different source."
                ],
            )

        byte_count, line_count = self._write_file_to_output(path, output_file)
        persisted_output_file = str(output_file)
        return LogSnapshotFilePayload(
            source_key=definition.source_key,
            source_type=definition.source_type,
            description=definition.description,
            target=definition.target,
            stream=definition.stream,
            parser_type=definition.parser_type,
            normalization_profile=definition.normalization_profile,
            default_noise_profile=definition.default_noise_profile,
            file_name=output_file.name,
            output_file=persisted_output_file,
            line_count=line_count,
            byte_count=byte_count,
        )

    def _collect_docker_source(
        self,
        definition: SourceDefinition,
        *,
        output_file: Path,
        since: str | None,
        until: str | None,
    ) -> LogSnapshotFilePayload | CollectSourceError:
        """Collect one docker-backed source through the Docker Engine API."""

        logs_kwargs: dict[str, int | str | datetime] = {}
        normalized_since = self.normalize_docker_time_filter(since)
        normalized_until = self.normalize_docker_time_filter(until)
        if normalized_since is not None:
            logs_kwargs["since"] = normalized_since
        if normalized_until is not None:
            logs_kwargs["until"] = normalized_until

        try:
            client: DockerClient = docker.from_env(  # type: ignore[attr-defined]
                timeout=DOCKER_LOG_TIMEOUT_SECONDS
            )
            container = client.containers.get(definition.target)
            byte_count = 0
            newline_count = 0
            trailing_byte: bytes = b""
            with output_file.open("wb") as handle:
                for chunk in container.logs(
                    follow=False,
                    timestamps=True,
                    stdout=True,
                    stderr=True,
                    stream=True,
                    **logs_kwargs,
                ):
                    handle.write(chunk)
                    byte_count += len(chunk)
                    newline_count += chunk.count(b"\n")
                    if chunk:
                        trailing_byte = chunk[-1:]
            persisted_output_file = str(output_file)
            if byte_count == 0:
                line_count = 0
            elif trailing_byte == b"\n":
                line_count = newline_count
            else:
                line_count = newline_count + 1
        except APIError as error:
            error_output = str(error).strip() or "Unknown docker error."
            return CollectSourceError(
                source_key=definition.source_key,
                source_type=definition.source_type,
                target=definition.target,
                description=definition.description,
                stream=definition.stream,
                parser_type=definition.parser_type,
                normalization_profile=definition.normalization_profile,
                default_noise_profile=definition.default_noise_profile,
                error=error_output,
                retry_tips=[
                    "Verify the container name in the manifest or retry with a different source."
                ],
            )
        except requests_exceptions.Timeout:
            error_message = f"Timed out collecting docker logs for {definition.target}."
            error_message += (
                " Retry with a narrower since/until window to limit the requested log output."
            )
            return CollectSourceError(
                source_key=definition.source_key,
                source_type=definition.source_type,
                target=definition.target,
                description=definition.description,
                stream=definition.stream,
                parser_type=definition.parser_type,
                normalization_profile=definition.normalization_profile,
                default_noise_profile=definition.default_noise_profile,
                error=error_message,
                retry_tips=[
                    "Retry with a narrower since/until window to keep docker log output bounded."
                ],
            )
        except DockerException:
            return CollectSourceError(
                source_key=definition.source_key,
                source_type=definition.source_type,
                target=definition.target,
                description=definition.description,
                stream=definition.stream,
                parser_type=definition.parser_type,
                normalization_profile=definition.normalization_profile,
                default_noise_profile=definition.default_noise_profile,
                error="Docker Engine API is not available in the current runtime.",
                retry_tips=["Retry in a runtime where the Docker socket is mounted and reachable."],
            )
        return LogSnapshotFilePayload(
            source_key=definition.source_key,
            source_type=definition.source_type,
            description=definition.description,
            target=definition.target,
            stream=definition.stream,
            parser_type=definition.parser_type,
            normalization_profile=definition.normalization_profile,
            default_noise_profile=definition.default_noise_profile,
            file_name=output_file.name,
            output_file=persisted_output_file,
            line_count=line_count,
            byte_count=byte_count,
        )

    @staticmethod
    def _build_response(
        *,
        project_name: str,
        workspace: SnapshotWorkspace,
        session_id: str | None,
        snapshot_dir: str,
        metadata_file: str,
        requested_since: str | None,
        requested_until: str | None,
        warnings: list[str],
        retry_tips: list[str],
        missing_source_keys: list[str],
        source_keys: list[str],
        collected_at: str,
        metadata: LogSnapshotMetadata,
        collected_results: list[LogSnapshotFilePayload | CollectSourceError],
    ) -> ProjectCollectLogsPayload:
        """Assemble the final agent-facing payload for one project collection.

        The snapshot metadata is the source of truth for successfully persisted
        files. Source-level errors are merged back into the response as
        `status="unavailable"` entries so callers can see partial collection
        failures without losing successful source metadata.
        """

        file_payloads_by_source_key = {item.source_key: item for item in metadata.files}
        sources: list[CollectedSourcePayload] = []
        for result in collected_results:
            if isinstance(result, CollectSourceError):
                sources.append(
                    CollectedSourcePayload(
                        source_key=result.source_key,
                        source_type=result.source_type,
                        target=result.target,
                        description=result.description,
                        stream=result.stream,
                        status="unavailable",
                        line_count=0,
                        byte_count=0,
                        output_file=None,
                        error=result.error,
                        retry_tips=result.retry_tips,
                    )
                )
                continue
            file_payload = file_payloads_by_source_key[result.source_key]
            sources.append(
                CollectedSourcePayload(
                    source_key=file_payload.source_key,
                    source_type=file_payload.source_type,
                    target=file_payload.target,
                    description=file_payload.description,
                    stream=file_payload.stream,
                    status="collected",
                    line_count=file_payload.line_count,
                    byte_count=file_payload.byte_count,
                    output_file=file_payload.output_file,
                    error=None,
                    retry_tips=[],
                )
            )

        return ProjectCollectLogsPayload(
            requested_project_name=project_name,
            project_name=project_name,
            workspace=workspace,
            session_id=None if workspace == "workflow" else session_id,
            snapshot_dir=snapshot_dir,
            metadata_file=metadata_file,
            persisted=True,
            requested_source_keys=[],
            requested_since=requested_since,
            requested_until=requested_until,
            next_step_tips=COLLECT_LOGS_NEXT_STEP_TIPS,
            warnings=warnings,
            retry_tips=retry_tips,
            unknown_requested_source_keys=missing_source_keys,
            resolved_source_keys=source_keys,
            collected_at=collected_at,
            sources=sources,
        )
