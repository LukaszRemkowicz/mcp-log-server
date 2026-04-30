"""Internal command wrappers for read-only container file inspection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from docker.errors import APIError, DockerException
from requests import exceptions as requests_exceptions

import docker

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
                f"Configured container {container_name!r} is not available in the current runtime."
            ) from error
        raise ValueError(error_output) from error
    except requests_exceptions.Timeout as error:
        raise ValueError(f"Timed out inspecting files in container {container_name!r}.") from error
    except DockerException as error:
        raise ValueError("Docker Engine API is not available in the current runtime.") from error

    exit_code = 0 if result.exit_code is None else int(result.exit_code)
    output = result.output.decode("utf-8", errors="replace")
    if exit_code != 0:
        normalized_output = output.strip()
        if "No such file or directory" in normalized_output or not normalized_output:
            raise ValueError("Requested container path was not found.")
        raise ValueError(normalized_output)
    return output


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


def stat_container_path(container_name: str, path: str) -> ContainerPathStat:
    """Return metadata for one existing regular file or directory inside a container."""

    output = _run_container_command(
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
    line = next((line for line in output.splitlines() if line.strip()), "")
    if not line:
        raise ValueError("Requested container path was not found.")
    return _parse_stat_line(line)


def read_container_file(
    container_name: str,
    path: str,
    *,
    max_bytes: int = MAX_CONTAINER_FILE_BYTES,
) -> tuple[str, bool]:
    """Read one regular file inside a container with a bounded response size."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer.")

    stat_payload = stat_container_path(container_name, path)
    if stat_payload.is_dir:
        raise ValueError("Requested container path is a directory, not a readable regular file.")

    output = _run_container_command(
        container_name,
        ["find", path, "-maxdepth", "0", "-type", "f", "-exec", "cat", "{}", ";"],
    )
    encoded_output = output.encode("utf-8")
    truncated = len(encoded_output) > max_bytes
    if truncated:
        output = encoded_output[:max_bytes].decode("utf-8", errors="ignore")
    return output, truncated


def list_container_directory(
    container_name: str,
    path: str,
) -> tuple[list[ContainerPathStat], bool]:
    """List immediate regular-file and directory children of one container directory."""

    directory_stat = stat_container_path(container_name, path)
    if not directory_stat.is_dir:
        raise ValueError("Requested container path is not a directory.")

    output = _run_container_command(
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
    entries: list[ContainerPathStat] = []
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        entries.append(_parse_stat_line(raw_line))
        if len(entries) >= MAX_DIRECTORY_ENTRIES:
            break

    entries.sort(key=lambda item: (not item.is_dir, item.path))
    return entries, len(output.splitlines()) > MAX_DIRECTORY_ENTRIES
