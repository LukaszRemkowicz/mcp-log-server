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
SAFE_COMPOSE_LABEL_KEYS = frozenset(
    {
        "com.docker.compose.project",
        "com.docker.compose.service",
        "com.docker.compose.container-number",
        "com.docker.compose.oneoff",
    }
)


@dataclass(slots=True)
class ContainerPathStat:
    """Metadata parsed from an approved container inspection command."""

    path: str
    is_dir: bool
    size: int
    mode: int
    modified_at: str | None


@dataclass(frozen=True, slots=True)
class ContainerHealth:
    """Structured Docker container runtime state for one manifest-approved source."""

    container_id: str
    container_name: str
    image: str | None
    docker_status: str | None
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


@dataclass(frozen=True, slots=True)
class ContainerDetailMount:
    """Curated mount metadata that avoids host source paths."""

    type: str | None
    destination: str | None
    mode: str | None
    rw: bool | None


@dataclass(frozen=True, slots=True)
class ContainerDetailNetwork:
    """Curated network metadata for one attached Docker network."""

    name: str
    ip_address: str | None
    aliases: list[str]


@dataclass(frozen=True, slots=True)
class ContainerDetailPort:
    """Curated published-port metadata for one container port."""

    private_port: str
    host_ip: str | None
    host_port: str | None


@dataclass(frozen=True, slots=True)
class ContainerRestartPolicy:
    """Curated Docker restart policy metadata."""

    name: str | None
    maximum_retry_count: int | None


@dataclass(frozen=True, slots=True)
class ContainerDetail:
    """Bounded Docker inspect-style metadata for one approved container."""

    health: ContainerHealth
    created_at: str | None
    env_var_names: list[str]
    label_keys: list[str]
    compose_labels: dict[str, str]
    restart_policy: ContainerRestartPolicy
    command: list[str]
    entrypoint: list[str]
    working_dir: str | None
    user: str | None
    ports: list[ContainerDetailPort]
    mounts: list[ContainerDetailMount]
    networks: list[ContainerDetailNetwork]
    health_log: list[dict[str, object]]


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

        container = DockerService._get_container(container_name)
        result = container.exec_run(command, stdout=True, stderr=True)

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
                message="Requested container path is a directory, not a readable regular file."
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

    @staticmethod
    def _get_container(container_name: str):
        """Return one Docker container object or raise a stable value error."""

        try:
            client: DockerClient = docker.from_env(  # type: ignore[attr-defined]
                timeout=DOCKER_INSPECTION_TIMEOUT_SECONDS
            )
            return client.containers.get(container_name)
        except APIError as error:
            error_output = str(error).strip() or "Unknown docker error."
            if "No such container" in error_output:
                raise ValueError(
                    f"Configured container {container_name!r} is not available "
                    "in the current runtime."
                ) from error
            raise ValueError(error_output) from error
        except requests_exceptions.Timeout as error:
            raise ValueError(f"Timed out inspecting container {container_name!r}.") from error
        except DockerException as error:
            raise ValueError(
                "Docker Engine API is not available in the current runtime."
            ) from error

    def inspect_container_health(
        self,
        container_name: str,
    ) -> ContainerHealth | DockerServiceError:
        """Return structured Docker runtime state for one container."""

        try:
            container = self._get_container(container_name)
        except ValueError as error:
            return DockerServiceError(message=str(error))

        return self._parse_container_health(container.attrs, container_name)

    @staticmethod
    def _parse_container_health(
        attrs: object,
        container_name: str,
    ) -> ContainerHealth:
        """Parse Docker SDK attrs into the bounded health payload."""

        state = attrs.get("State") if isinstance(attrs, dict) else {}
        if not isinstance(state, dict):
            state = {}
        config = attrs.get("Config") if isinstance(attrs, dict) else {}
        if not isinstance(config, dict):
            config = {}
        health = state.get("Health")
        health_status = None
        if isinstance(health, dict):
            health_status_value = health.get("Status")
            health_status = str(health_status_value) if health_status_value is not None else None

        raw_name = attrs.get("Name") if isinstance(attrs, dict) else None
        resolved_name = str(raw_name).lstrip("/") if raw_name else container_name
        raw_exit_code = state.get("ExitCode")
        exit_code = int(raw_exit_code) if isinstance(raw_exit_code, int) else None
        raw_restart_count = attrs.get("RestartCount") if isinstance(attrs, dict) else None
        restart_count = int(raw_restart_count) if isinstance(raw_restart_count, int) else None
        raw_error = state.get("Error")
        state_error = str(raw_error) if raw_error not in {None, ""} else ""
        running = bool(state.get("Running", False))
        finished_at = None
        if not running and state.get("FinishedAt") is not None:
            finished_at = str(state.get("FinishedAt"))

        return ContainerHealth(
            container_id=str(attrs.get("Id", "")) if isinstance(attrs, dict) else "",
            container_name=resolved_name,
            image=str(config.get("Image")) if config.get("Image") is not None else None,
            docker_status=(str(state.get("Status")) if state.get("Status") is not None else None),
            health_status=health_status,
            running=running,
            restarting=bool(state.get("Restarting", False)),
            paused=bool(state.get("Paused", False)),
            dead=bool(state.get("Dead", False)),
            exit_code=exit_code,
            error=state_error,
            restart_count=restart_count,
            started_at=str(state.get("StartedAt")) if state.get("StartedAt") is not None else None,
            finished_at=finished_at,
        )

    def inspect_container_detail(
        self,
        container_name: str,
    ) -> ContainerDetail | DockerServiceError:
        """Return curated Docker inspect-style metadata for one container."""

        try:
            container = self._get_container(container_name)
        except ValueError as error:
            return DockerServiceError(message=str(error))

        attrs = container.attrs
        config = attrs.get("Config") if isinstance(attrs, dict) else {}
        if not isinstance(config, dict):
            config = {}
        state = attrs.get("State") if isinstance(attrs, dict) else {}
        if not isinstance(state, dict):
            state = {}

        created_at = None
        if isinstance(attrs, dict) and attrs.get("Created") is not None:
            created_at = str(attrs.get("Created"))

        return ContainerDetail(
            health=self._parse_container_health(attrs, container_name),
            created_at=created_at,
            env_var_names=self._extract_env_var_names(config.get("Env")),
            label_keys=self._extract_label_keys(config.get("Labels")),
            compose_labels=self._extract_compose_labels(config.get("Labels")),
            restart_policy=self._extract_restart_policy(
                attrs.get("HostConfig") if isinstance(attrs, dict) else None
            ),
            command=self._extract_string_list(config.get("Cmd")),
            entrypoint=self._extract_string_list(config.get("Entrypoint")),
            working_dir=self._extract_optional_string(config.get("WorkingDir")),
            user=self._extract_optional_string(config.get("User")),
            ports=self._extract_ports(
                attrs.get("NetworkSettings") if isinstance(attrs, dict) else None
            ),
            mounts=self._extract_mounts(attrs.get("Mounts") if isinstance(attrs, dict) else None),
            networks=self._extract_networks(
                attrs.get("NetworkSettings") if isinstance(attrs, dict) else None
            ),
            health_log=self._extract_health_log(state.get("Health")),
        )

    @staticmethod
    def _extract_env_var_names(env: object) -> list[str]:
        """Return environment variable names without exposing values."""

        if not isinstance(env, list):
            return []
        names: list[str] = []
        for item in env:
            if not isinstance(item, str) or "=" not in item:
                continue
            name = item.split("=", 1)[0]
            if name:
                names.append(name)
        return names

    @staticmethod
    def _extract_label_keys(labels: object) -> list[str]:
        """Return Docker label keys without exposing label values."""

        if not isinstance(labels, dict):
            return []
        return [str(key) for key in labels.keys()]

    @staticmethod
    def _extract_compose_labels(labels: object) -> dict[str, str]:
        """Return values only for selected low-risk Docker Compose labels."""

        if not isinstance(labels, dict):
            return {}
        results: dict[str, str] = {}
        for key, value in labels.items():
            label_key = str(key)
            if label_key in SAFE_COMPOSE_LABEL_KEYS and value is not None:
                results[label_key] = str(value)
        return dict(sorted(results.items()))

    @staticmethod
    def _extract_restart_policy(host_config: object) -> ContainerRestartPolicy:
        """Return Docker restart policy metadata."""

        if not isinstance(host_config, dict):
            return ContainerRestartPolicy(name=None, maximum_retry_count=None)
        restart_policy = host_config.get("RestartPolicy")
        if not isinstance(restart_policy, dict):
            return ContainerRestartPolicy(name=None, maximum_retry_count=None)
        raw_retry_count = restart_policy.get("MaximumRetryCount")
        retry_count = raw_retry_count if isinstance(raw_retry_count, int) else None
        name = restart_policy.get("Name")
        return ContainerRestartPolicy(
            name=str(name) if name is not None else None,
            maximum_retry_count=retry_count,
        )

    @staticmethod
    def _extract_optional_string(value: object) -> str | None:
        """Return non-empty strings as strings and normalize blanks to null."""

        if not isinstance(value, str) or not value:
            return None
        return value

    @staticmethod
    def _extract_string_list(value: object) -> list[str]:
        """Normalize Docker string/list command fields into a list."""

        if isinstance(value, str):
            return [value] if value else []
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]

    @staticmethod
    def _extract_ports(network_settings: object) -> list[ContainerDetailPort]:
        """Return bounded published port metadata."""

        if not isinstance(network_settings, dict):
            return []
        ports = network_settings.get("Ports")
        if not isinstance(ports, dict):
            return []
        results: list[ContainerDetailPort] = []
        for private_port, bindings in ports.items():
            if isinstance(bindings, list) and bindings:
                for binding in bindings:
                    if not isinstance(binding, dict):
                        continue
                    results.append(
                        ContainerDetailPort(
                            private_port=str(private_port),
                            host_ip=(
                                str(binding.get("HostIp"))
                                if binding.get("HostIp") is not None
                                else None
                            ),
                            host_port=(
                                str(binding.get("HostPort"))
                                if binding.get("HostPort") is not None
                                else None
                            ),
                        )
                    )
                continue
            results.append(
                ContainerDetailPort(
                    private_port=str(private_port),
                    host_ip=None,
                    host_port=None,
                )
            )
        return sorted(results, key=lambda item: item.private_port)

    @staticmethod
    def _extract_mounts(mounts: object) -> list[ContainerDetailMount]:
        """Return bounded mount metadata without host source paths."""

        if not isinstance(mounts, list):
            return []
        results: list[ContainerDetailMount] = []
        for item in mounts:
            if not isinstance(item, dict):
                continue
            results.append(
                ContainerDetailMount(
                    type=str(item.get("Type")) if item.get("Type") is not None else None,
                    destination=(
                        str(item.get("Destination"))
                        if item.get("Destination") is not None
                        else None
                    ),
                    mode=str(item.get("Mode")) if item.get("Mode") is not None else None,
                    rw=item.get("RW") if isinstance(item.get("RW"), bool) else None,
                )
            )
        return results

    @staticmethod
    def _extract_networks(network_settings: object) -> list[ContainerDetailNetwork]:
        """Return network names and container IPs from Docker metadata."""

        if not isinstance(network_settings, dict):
            return []
        networks = network_settings.get("Networks")
        if not isinstance(networks, dict):
            return []
        results: list[ContainerDetailNetwork] = []
        for name, value in networks.items():
            if not isinstance(value, dict):
                continue
            ip_address = value.get("IPAddress")
            aliases = value.get("Aliases")
            results.append(
                ContainerDetailNetwork(
                    name=str(name),
                    ip_address=str(ip_address) if ip_address is not None else None,
                    aliases=(
                        [item for item in aliases if isinstance(item, str)]
                        if isinstance(aliases, list)
                        else []
                    ),
                )
            )
        return results

    @staticmethod
    def _extract_health_log(health: object) -> list[dict[str, object]]:
        """Return a small bounded healthcheck log view."""

        if not isinstance(health, dict):
            return []
        entries = health.get("Log")
        if not isinstance(entries, list):
            return []
        results: list[dict[str, object]] = []
        for item in entries[-5:]:
            if not isinstance(item, dict):
                continue
            output = item.get("Output")
            results.append(
                {
                    "start": item.get("Start"),
                    "end": item.get("End"),
                    "exit_code": item.get("ExitCode"),
                    "output": str(output)[:4000] if output is not None else None,
                }
            )
        return results
