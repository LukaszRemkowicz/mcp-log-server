"""Deterministic source adapters for file-backed and docker-backed log collection."""

from __future__ import annotations

import re
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from docker.errors import APIError, DockerException
from requests import exceptions as requests_exceptions

import docker
from manifests.models import SourceDefinition
from tools.models import CollectedSourcePayload

if TYPE_CHECKING:
    from docker.client import DockerClient  # type: ignore[import-not-found]

MAX_TAIL_LINES = 1000
DOCKER_LOG_TIMEOUT_SECONDS = 15


class LogSourceCollectionService:
    """Collect raw log content from one manifest source definition.

    Responsibility:

    - adapt one manifest source into deterministic collected text
    - handle backend-specific collection details for `file` and `docker`
    - normalize caller-facing time filters into Docker SDK inputs
    - keep low-level collection behavior separate from snapshot persistence
      and MCP response shaping

    This service does not decide:

    - which project/source keys are authorized
    - where collected output should be persisted
    - how the final MCP payload should be assembled

    Those higher-level concerns belong to `LogCollectionService` and
    `LogSnapshotService`.
    """

    def __init__(self, *, max_tail_lines: int = MAX_TAIL_LINES) -> None:
        self.max_tail_lines = max_tail_lines

    def limit_tail_lines(self, tail_lines: int) -> int:
        """Keep collection size bounded for deterministic tool responses."""

        return max(1, min(tail_lines, self.max_tail_lines))

    def collect_source(
        self,
        definition: SourceDefinition,
        tail_lines: int | None,
        *,
        timestamps: bool,
        since: str | None,
        until: str | None,
    ) -> CollectedSourcePayload:
        """Collect one manifest source through the supported deterministic adapters."""

        if definition.source_type == "file":
            return self._collect_file_source(definition, tail_lines)
        return self._collect_docker_source(
            definition,
            tail_lines,
            timestamps=timestamps,
            since=since,
            until=until,
        )

    @staticmethod
    def _read_full_file(path: Path) -> str:
        """Read the full contents of a file-backed source."""

        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _read_file_tail(path: Path, tail_lines: int) -> str:
        """Read the last `tail_lines` lines from a file-backed source as-is."""

        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return "".join(deque(handle, maxlen=tail_lines))

    @staticmethod
    def normalize_docker_time_filter(value: str | None) -> datetime | int | None:
        """Normalize one agent-facing docker time filter into a Docker SDK value."""

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
        tail_lines: int | None,
    ) -> CollectedSourcePayload:
        """Collect one file-backed source from the path declared in the manifest."""

        path = Path(definition.target)
        if not path.exists():
            return CollectedSourcePayload(
                source_key=definition.source_key,
                source_type=definition.source_type,
                target=definition.target,
                description=definition.description,
                stream=definition.stream,
                status="unavailable",
                line_count=0,
                byte_count=0,
                content_truncated=False,
                content="",
                output_file=None,
                error=f"File source not found: {definition.target}",
                retry_tips=[
                    "Verify the file path in the manifest or retry with a different source."
                ],
            )

        if tail_lines is None:
            content = self._read_full_file(path)
        else:
            content = self._read_file_tail(path, tail_lines)
        byte_count = len(content.encode("utf-8"))
        line_count = 0 if not content else len(content.splitlines())
        return CollectedSourcePayload(
            source_key=definition.source_key,
            source_type=definition.source_type,
            target=definition.target,
            description=definition.description,
            stream=definition.stream,
            status="collected",
            line_count=line_count,
            byte_count=byte_count,
            content_truncated=False,
            content=content,
            output_file=None,
            error=None,
            retry_tips=[],
        )

    def _collect_docker_source(
        self,
        definition: SourceDefinition,
        tail_lines: int | None,
        *,
        timestamps: bool,
        since: str | None,
        until: str | None,
    ) -> CollectedSourcePayload:
        """Collect one docker-backed source through the Docker Engine API."""

        logs_kwargs: dict[str, bool | int | str | datetime] = {
            "timestamps": timestamps,
            "stdout": True,
            "stderr": True,
        }
        if tail_lines is not None:
            logs_kwargs["tail"] = tail_lines
        normalized_since = self.normalize_docker_time_filter(since)
        normalized_until = self.normalize_docker_time_filter(until)
        if normalized_since is not None:
            logs_kwargs["since"] = normalized_since
        if normalized_until is not None:
            logs_kwargs["until"] = normalized_until

        try:
            # docker-py exposes from_env at runtime, but the typing is incomplete here.
            client: DockerClient = docker.from_env(  # type: ignore[attr-defined]
                timeout=DOCKER_LOG_TIMEOUT_SECONDS
            )
            content_bytes = client.containers.get(definition.target).logs(**logs_kwargs)
        except APIError as error:
            error_output = str(error).strip() or "Unknown docker error."
            return CollectedSourcePayload(
                source_key=definition.source_key,
                source_type=definition.source_type,
                target=definition.target,
                description=definition.description,
                stream=definition.stream,
                status="unavailable",
                line_count=0,
                byte_count=0,
                content_truncated=False,
                content="",
                output_file=None,
                error=error_output,
                retry_tips=[
                    "Verify the container name in the manifest or retry with a different source."
                ],
            )
        except requests_exceptions.Timeout:
            retry_tips = []
            error_message = f"Timed out collecting docker logs for {definition.target}."
            if tail_lines is None:
                error_message += " Retry with tail_lines to limit the requested log output."
                retry_tips.append(
                    "Retry with tail_lines <= "
                    f"{self.max_tail_lines} to keep docker log output bounded."
                )
            return CollectedSourcePayload(
                source_key=definition.source_key,
                source_type=definition.source_type,
                target=definition.target,
                description=definition.description,
                stream=definition.stream,
                status="unavailable",
                line_count=0,
                byte_count=0,
                content_truncated=False,
                content="",
                output_file=None,
                error=error_message,
                retry_tips=retry_tips,
            )
        except DockerException:
            return CollectedSourcePayload(
                source_key=definition.source_key,
                source_type=definition.source_type,
                target=definition.target,
                description=definition.description,
                stream=definition.stream,
                status="unavailable",
                line_count=0,
                byte_count=0,
                content_truncated=False,
                content="",
                output_file=None,
                error="Docker Engine API is not available in the current runtime.",
                retry_tips=["Retry in a runtime where the Docker socket is mounted and reachable."],
            )

        content = content_bytes.decode("utf-8", errors="replace")
        byte_count = len(content.encode("utf-8"))
        return CollectedSourcePayload(
            source_key=definition.source_key,
            source_type=definition.source_type,
            target=definition.target,
            description=definition.description,
            stream=definition.stream,
            status="collected",
            line_count=0 if not content else len(content.splitlines()),
            byte_count=byte_count,
            content_truncated=False,
            content=content,
            output_file=None,
            error=None,
            retry_tips=[],
        )


_DOCKER_DURATION_PATTERN = re.compile(r"^(?P<value>\d+)(?P<unit>[smhd])$")
