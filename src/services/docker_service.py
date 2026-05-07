"""Docker-backed services for approved container file inspection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from docker.errors import APIError, DockerException
from pydantic import BaseModel
from requests import exceptions as requests_exceptions

import docker
from manifests.models import SourceDefinition

if TYPE_CHECKING:
    from docker.client import DockerClient  # type: ignore[import-not-found]

DOCKER_INSPECTION_TIMEOUT_SECONDS = 15
MAX_CONTAINER_FILE_BYTES = 200_000
MAX_DIRECTORY_ENTRIES = 200


@dataclass(slots=True)
class ContainerPathStat:
    """Metadata parsed from an approved container inspection command."""

    path: str
    is_dir: bool
    size: int
    mode: int
    modified_at: str | None


class DockerServiceError(BaseModel):
    """Expected Docker inspection failure returned to the MCP tool layer."""

    message: str


class DockerService:
    """Run the small approved Docker operations needed by MCP tools.

    This service is intentionally narrower than a generic Docker wrapper. It
    exists for read-only container file inspection, maps Docker runtime errors
    into `DockerServiceError`, and exposes only the command shapes used by the
    manifest-bounded container inspection tools.
    """

    def normalize_container_path(self, path: str) -> str:
        """Normalize one requested container path into a safe absolute POSIX path.

        Container inspection accepts explicit paths inside a container, not
        shell fragments or ambiguous relative locations. This method enforces
        the path contract before manifest whitelist checks and Docker commands:

        - the path must be absolute
        - parent-directory traversal is rejected
        - the returned value is a normalized POSIX path string
        """

        stripped_path = path.strip()
        if not stripped_path.startswith("/"):
            raise ValueError("Container inspection path must be an absolute path.")

        raw_path = PurePosixPath(stripped_path)
        if ".." in raw_path.parts:
            raise ValueError(
                "Container inspection path may not include parent directory traversal."
            )

        normalized_path = str(raw_path)
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        return normalized_path

    def normalize_container_path_or_error(self, path: str) -> str | DockerServiceError:
        """Return a normalized container path or a structured validation error."""

        try:
            return self.normalize_container_path(path)
        except ValueError as error:
            return DockerServiceError(message=str(error))

    def container_path_is_allowed(self, definition: SourceDefinition, path: str) -> bool:
        """Return whether a container path stays inside manifest inspection roots.

        The MCP tool layer decides how to report rejected requests, but this
        service owns the inspection safety decision:

        - normalize the requested path
        - normalize each manifest `inspect_path_prefixes` entry
        - allow exact prefix matches
        - allow children under an approved prefix
        - reject everything else before Docker commands run
        """

        normalized_path = self.normalize_container_path(path)
        for prefix in definition.inspect_path_prefixes:
            normalized_prefix = self.normalize_container_path(prefix)
            if normalized_path == normalized_prefix:
                return True
            if normalized_path.startswith(f"{normalized_prefix.rstrip('/')}/"):
                return True
        return False

    def resolve_container_directory_path(
        self,
        definition: SourceDefinition,
        path: str | None,
    ) -> str:
        """Return the requested directory path or the source inspection root.

        Directory listing is the discovery entry point for agents. When the
        caller does not know a concrete path yet, an omitted or blank `path`
        resolves to the first manifest-approved `inspect_path_prefixes` entry.
        Explicit paths are normalized and still checked by the caller against
        the manifest whitelist before Docker commands run.
        """

        if path is None or not path.strip():
            return self.normalize_container_path(definition.inspect_path_prefixes[0])
        return self.normalize_container_path(path)

    def resolve_container_directory_path_or_error(
        self,
        definition: SourceDefinition,
        path: str | None,
    ) -> str | DockerServiceError:
        """Return a directory path or a structured validation error."""

        try:
            return self.resolve_container_directory_path(definition, path)
        except ValueError as error:
            return DockerServiceError(message=str(error))

    @staticmethod
    def _run_container_command(container_name: str, command: list[str]) -> str:
        """Run one approved command inside a container and return UTF-8 output."""

        try:
            # docker-py exposes from_env at runtime, but the typing is incomplete here.
            client: DockerClient = docker.from_env(  # type: ignore[attr-defined]
                timeout=DOCKER_INSPECTION_TIMEOUT_SECONDS
            )
            container = client.containers.get(container_name)
            result = container.exec_run(command, stdout=True, stderr=True)
        except APIError as error:
            error_output = str(error).strip() or "Unknown docker error."
            if "No such container" in error_output:
                raise ValueError(
                    f"Configured container {container_name!r} is not available "
                    "in the current runtime."
                ) from error
            raise ValueError(error_output) from error
        except requests_exceptions.Timeout as error:
            raise ValueError(
                f"Timed out inspecting files in container {container_name!r}."
            ) from error
        except DockerException as error:
            raise ValueError(
                "Docker Engine API is not available in the current runtime."
            ) from error

        exit_code = 0 if result.exit_code is None else int(result.exit_code)
        output = result.output.decode("utf-8", errors="replace")
        if exit_code != 0:
            normalized_output = output.strip()
            if "No such file or directory" in normalized_output or not normalized_output:
                raise ValueError("Requested container path was not found.")
            raise ValueError(normalized_output)
        return output

    @staticmethod
    def _parse_stat_line(raw_line: str) -> ContainerPathStat:
        """Parse one tab-separated stat line emitted by the approved stat command."""

        file_type, size, mode, modified_epoch, path = raw_line.rstrip("\n").split("\t", 4)
        modified_at = None
        if modified_epoch.isdigit():
            modified_at = datetime.fromtimestamp(int(modified_epoch), UTC).isoformat()
        return ContainerPathStat(
            path=path,
            is_dir=file_type == "directory",
            size=int(size),
            mode=int(mode, 8),
            modified_at=modified_at,
        )

    def stat_container_path(
        self,
        container_name: str,
        path: str,
    ) -> ContainerPathStat | DockerServiceError:
        """Run the approved container stat command and parse one path result.

        The method executes a fixed `find <path> -maxdepth 0 ... -exec stat`
        command inside the selected container. That command only matches the
        exact requested path when it is a regular file or directory, then emits
        tab-separated stat fields. The output is parsed into `ContainerPathStat`
        so callers receive structured metadata instead of raw command text.
        """

        try:
            output = self._run_container_command(
                container_name,
                [
                    "find",
                    path,
                    "-maxdepth",
                    "0",
                    "(",
                    "-type",
                    "f",
                    "-o",
                    "-type",
                    "d",
                    ")",
                    "-exec",
                    "stat",
                    "-c",
                    "%F\t%s\t%a\t%Y\t%n",
                    "{}",
                    ";",
                ],
            )
        except ValueError as error:
            return DockerServiceError(message=str(error))

        line = next((line for line in output.splitlines() if line.strip()), "")
        if not line:
            return DockerServiceError(message="Requested container path was not found.")
        try:
            return self._parse_stat_line(line)
        except ValueError as error:
            return DockerServiceError(message=str(error))

    def read_container_file(
        self,
        container_name: str,
        path: str,
        *,
        max_bytes: int = MAX_CONTAINER_FILE_BYTES,
    ) -> tuple[str, bool] | DockerServiceError:
        """Read one regular file inside a container with a bounded response size."""

        if max_bytes < 1:
            return DockerServiceError(message="max_bytes must be a positive integer.")

        stat_payload = self.stat_container_path(container_name, path)
        if isinstance(stat_payload, DockerServiceError):
            return stat_payload
        if stat_payload.is_dir:
            return DockerServiceError(
                message=("Requested container path is a directory, not a readable regular file.")
            )

        try:
            output = self._run_container_command(
                container_name,
                ["find", path, "-maxdepth", "0", "-type", "f", "-exec", "cat", "{}", ";"],
            )
        except ValueError as error:
            return DockerServiceError(message=str(error))
        encoded_output = output.encode("utf-8")
        truncated = len(encoded_output) > max_bytes
        if truncated:
            output = encoded_output[:max_bytes].decode("utf-8", errors="ignore")
        return output, truncated

    def list_container_directory(
        self,
        container_name: str,
        path: str,
    ) -> tuple[list[ContainerPathStat], bool] | DockerServiceError:
        """List one container path like `ls -la`.

        Directory paths return immediate regular-file and directory children.
        File paths return a single metadata entry for that file.
        """

        path_stat = self.stat_container_path(container_name, path)
        if isinstance(path_stat, DockerServiceError):
            return path_stat
        if not path_stat.is_dir:
            return [path_stat], False

        try:
            output = self._run_container_command(
                container_name,
                [
                    "find",
                    path,
                    "-mindepth",
                    "1",
                    "-maxdepth",
                    "1",
                    "(",
                    "-type",
                    "f",
                    "-o",
                    "-type",
                    "d",
                    ")",
                    "-exec",
                    "stat",
                    "-c",
                    "%F\t%s\t%a\t%Y\t%n",
                    "{}",
                    ";",
                ],
            )
        except ValueError as error:
            return DockerServiceError(message=str(error))
        entries: list[ContainerPathStat] = []
        for raw_line in output.splitlines():
            if not raw_line.strip():
                continue
            entries.append(self._parse_stat_line(raw_line))
            if len(entries) >= MAX_DIRECTORY_ENTRIES:
                break

        entries.sort(key=lambda item: (not item.is_dir, item.path))
        return entries, len(output.splitlines()) > MAX_DIRECTORY_ENTRIES
