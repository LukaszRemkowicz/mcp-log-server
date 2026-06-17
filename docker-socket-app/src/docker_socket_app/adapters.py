"""Docker SDK adapter for the Docker socket app."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from docker import from_env
from docker.errors import APIError, DockerException
from requests import exceptions as requests_exceptions

from .schemas import DockerBackend

DOCKER_TIMEOUT_SECONDS = 15
MAX_LOG_BYTES = 1_000_000
MAX_FILE_BYTES = 200_000
MAX_DIRECTORY_ENTRIES = 200
MAX_VPS_CONTAINERS = 200
MAX_VPS_VOLUMES = 200
ANONYMOUS_VOLUME_NAME_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_COMPOSE_LABEL_KEYS = frozenset(
    {
        "com.docker.compose.project",
        "com.docker.compose.service",
        "com.docker.compose.container-number",
        "com.docker.compose.oneoff",
        "com.docker.compose.volume",
    }
)


class DockerBackendError(RuntimeError):
    """Expected Docker SDK operation failure."""


class DockerSdkAdapter(DockerBackend):
    """Run the socket app's fixed read-only operations with the Docker SDK.

    This adapter is the only layer that touches the real Docker daemon. It is
    deliberately narrower than the Docker SDK itself: each public method maps
    to one supported socket operation, returns a JSON-serializable dictionary,
    and avoids Docker mutation APIs.

    Container file and directory reads use bounded `exec_run` calls with
    explicit command arguments. They are not a generic shell feature; callers
    cannot provide arbitrary commands through the socket protocol.
    """

    def __init__(self, client: Any | None = None) -> None:
        self.client = client or from_env(timeout=DOCKER_TIMEOUT_SECONDS)

    def container_logs(
        self,
        *,
        container_name: str,
        since: str | None = None,
        until: str | None = None,
        tail: int | None = None,
    ) -> dict[str, Any]:
        """Return bounded timestamped logs for one container."""

        kwargs: dict[str, Any] = {}
        if since is not None:
            kwargs["since"] = self._parse_log_timestamp(since, "since")
        if until is not None:
            kwargs["until"] = self._parse_log_timestamp(until, "until")
        if tail is not None:
            kwargs["tail"] = tail

        try:
            container = self.client.containers.get(container_name)
            chunks = container.logs(
                follow=False,
                timestamps=True,
                stdout=True,
                stderr=True,
                stream=True,
                **kwargs,
            )
        except requests_exceptions.Timeout as error:
            raise DockerBackendError(
                f"Timed out collecting docker logs for {container_name}."
            ) from error
        except DockerException as error:
            raise DockerBackendError(str(error).strip() or "Docker operation failed.") from error

        lines: list[str] = []
        total_bytes = 0
        truncated = False
        for chunk in chunks:
            raw = chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8")
            total_bytes += len(raw)
            if total_bytes > MAX_LOG_BYTES:
                truncated = True
                break
            lines.append(raw.decode("utf-8", errors="replace").rstrip("\n"))
        return {"container_name": container_name, "logs": lines, "truncated": truncated}

    @staticmethod
    def _parse_log_timestamp(value: str, param_name: str) -> datetime:
        """Return a Docker SDK-compatible timestamp from a JSON timestamp string."""

        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise DockerBackendError(
                f"Parameter '{param_name}' must be an ISO 8601 timestamp."
            ) from error
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def container_health(self, *, container_name: str) -> dict[str, Any]:
        """Return bounded runtime state for one container."""

        container = self._get_container(container_name)
        return self._parse_container_health(container.attrs, container_name)

    def container_detail(self, *, container_name: str) -> dict[str, Any]:
        """Return curated inspect-style metadata for one container."""

        container = self._get_container(container_name)
        attrs = container.attrs if isinstance(container.attrs, dict) else {}
        config = attrs.get("Config") if isinstance(attrs.get("Config"), dict) else {}
        host_config = attrs.get("HostConfig") if isinstance(attrs.get("HostConfig"), dict) else {}
        return {
            "health": self._parse_container_health(attrs, container_name),
            "created_at": self._optional_string(attrs.get("Created")),
            "env_var_names": self._extract_env_var_names(config.get("Env")),
            "label_keys": sorted(str(key) for key in (config.get("Labels") or {}).keys()),
            "compose_labels": self._extract_compose_labels(config.get("Labels")),
            "restart_policy": host_config.get("RestartPolicy") or {},
            "command": self._extract_string_list(config.get("Cmd")),
            "entrypoint": self._extract_string_list(config.get("Entrypoint")),
            "working_dir": self._optional_string(config.get("WorkingDir")),
            "user": self._optional_string(config.get("User")),
            "ports": self._extract_ports(attrs.get("NetworkSettings")),
            "mounts": self._extract_mounts(attrs.get("Mounts")),
            "networks": self._extract_networks(attrs.get("NetworkSettings")),
            "health_log": self._extract_health_log(attrs.get("State")),
            "env_vars": self._extract_env_vars(config.get("Env")),
        }

    def container_path_stat(self, *, container_name: str, path: str) -> dict[str, Any]:
        """Return stat metadata for one regular file or directory inside a container."""

        normalized_path = self._normalize_container_path(path)
        output = self._run_container_command(
            container_name,
            [
                "find",
                normalized_path,
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
            raise DockerBackendError("Requested container path was not found.")
        return self._parse_stat_line(line)

    def container_file_read(
        self,
        *,
        container_name: str,
        path: str,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Read one bounded regular file inside a container."""

        limit = max_bytes or MAX_FILE_BYTES
        if limit < 1:
            raise DockerBackendError("max_bytes must be a positive integer.")
        path_stat = self.container_path_stat(container_name=container_name, path=path)
        if path_stat["is_dir"]:
            raise DockerBackendError(
                "Requested container path is a directory, not a readable regular file."
            )
        normalized_path = self._normalize_container_path(path)
        output = self._run_container_command(
            container_name,
            ["find", normalized_path, "-maxdepth", "0", "-type", "f", "-exec", "cat", "{}", ";"],
        )
        encoded = output.encode("utf-8")
        truncated = len(encoded) > limit
        if truncated:
            output = encoded[:limit].decode("utf-8", errors="ignore")
        return {"path": normalized_path, "content": output, "truncated": truncated}

    def container_directory_list(
        self,
        *,
        container_name: str,
        path: str,
        max_entries: int | None = None,
    ) -> dict[str, Any]:
        """List one directory or return one file stat."""

        limit = max_entries or MAX_DIRECTORY_ENTRIES
        path_stat = self.container_path_stat(container_name=container_name, path=path)
        if not path_stat["is_dir"]:
            return {"path": path_stat["path"], "entries": [path_stat], "truncated": False}

        output = self._run_container_command(
            container_name,
            [
                "find",
                path_stat["path"],
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
        entries = [self._parse_stat_line(line) for line in output.splitlines() if line.strip()]
        entries.sort(key=lambda item: (not item["is_dir"], item["path"]))
        return {
            "path": path_stat["path"],
            "entries": entries[:limit],
            "truncated": len(entries) > limit,
        }

    def vps_containers_inventory(self) -> dict[str, Any]:
        """Return bounded, redacted Docker container inventory."""

        try:
            containers = self.client.containers.list(all=True)
        except requests_exceptions.Timeout as error:
            raise DockerBackendError("Timed out listing Docker containers.") from error
        except DockerException as error:
            raise DockerBackendError(str(error).strip() or "Docker operation failed.") from error

        rows = [self._parse_container_inventory(container.attrs) for container in containers]
        rows.sort(key=lambda item: item["container_name"])
        return {
            "containers": rows[:MAX_VPS_CONTAINERS],
            "truncated": len(rows) > MAX_VPS_CONTAINERS,
        }

    def vps_volumes_inventory(
        self,
        *,
        dangling_only: bool = False,
        anonymous_only: bool = False,
        name_prefix: str | None = None,
    ) -> dict[str, Any]:
        """Return bounded, redacted Docker volume inventory."""

        try:
            filters = {"dangling": True} if dangling_only else None
            volumes = (
                self.client.volumes.list(filters=filters)
                if filters is not None
                else self.client.volumes.list()
            )
        except requests_exceptions.Timeout as error:
            raise DockerBackendError("Timed out listing Docker volumes.") from error
        except DockerException as error:
            raise DockerBackendError(str(error).strip() or "Docker operation failed.") from error

        rows: list[dict[str, Any]] = []
        for volume in volumes:
            row = self._parse_volume_inventory(volume.attrs)
            if anonymous_only and not ANONYMOUS_VOLUME_NAME_PATTERN.fullmatch(row["volume_name"]):
                continue
            if name_prefix is not None and not row["volume_name"].startswith(name_prefix):
                continue
            rows.append(row)
        rows.sort(key=lambda item: item["volume_name"])
        return {"volumes": rows[:MAX_VPS_VOLUMES], "truncated": len(rows) > MAX_VPS_VOLUMES}

    def _get_container(self, container_name: str) -> Any:
        try:
            return self.client.containers.get(container_name)
        except APIError as error:
            output = str(error).strip() or "Unknown docker error."
            if "No such container" in output:
                raise DockerBackendError(
                    f"Configured container {container_name!r} is not available "
                    "in the current runtime."
                ) from error
            raise DockerBackendError(output) from error
        except requests_exceptions.Timeout as error:
            raise DockerBackendError(
                f"Timed out inspecting container {container_name!r}."
            ) from error
        except DockerException as error:
            raise DockerBackendError(str(error).strip() or "Docker operation failed.") from error

    def _run_container_command(self, container_name: str, command: list[str]) -> str:
        container = self._get_container(container_name)
        result = container.exec_run(command, stdout=True, stderr=True)
        exit_code = 0 if result.exit_code is None else int(result.exit_code)
        output = result.output.decode("utf-8", errors="replace")
        if exit_code != 0:
            normalized_output = output.strip()
            if "No such file or directory" in normalized_output or not normalized_output:
                raise DockerBackendError("Requested container path was not found.")
            raise DockerBackendError(normalized_output)
        return output

    @staticmethod
    def _normalize_container_path(path: str) -> str:
        stripped_path = path.strip()
        if not stripped_path.startswith("/"):
            raise DockerBackendError("Container inspection path must be an absolute path.")
        raw_path = PurePosixPath(stripped_path)
        if ".." in raw_path.parts:
            raise DockerBackendError(
                "Container inspection path may not include parent directory traversal."
            )
        normalized_path = str(raw_path)
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        return normalized_path

    @staticmethod
    def _parse_stat_line(raw_line: str) -> dict[str, Any]:
        file_type, size, mode, modified_epoch, path = raw_line.rstrip("\n").split("\t", 4)
        modified_at = None
        if modified_epoch.isdigit():
            modified_at = datetime.fromtimestamp(int(modified_epoch), UTC).isoformat()
        return {
            "path": path,
            "is_dir": file_type == "directory",
            "size": int(size),
            "mode": int(mode, 8),
            "modified_at": modified_at,
        }

    def _parse_container_inventory(self, attrs: object) -> dict[str, Any]:
        attrs_dict = attrs if isinstance(attrs, dict) else {}
        config = attrs_dict.get("Config") if isinstance(attrs_dict.get("Config"), dict) else {}
        health = self._parse_container_health(attrs_dict, "")
        container_id = health["container_id"]
        return {
            "container_id": container_id,
            "short_container_id": container_id[:12],
            "container_name": health["container_name"],
            "image": health["image"],
            "command": self._extract_string_list(config.get("Cmd")),
            "created_at": self._optional_string(attrs_dict.get("Created")),
            "docker_status": health["docker_status"],
            "state": health["docker_status"],
            "health_status": health["health_status"],
            "running": health["running"],
            "restarting": health["restarting"],
            "paused": health["paused"],
            "dead": health["dead"],
            "exit_code": health["exit_code"],
            "error": health["error"],
            "restart_count": health["restart_count"],
            "started_at": health["started_at"],
            "finished_at": health["finished_at"],
            "compose_labels": self._extract_compose_labels(config.get("Labels")),
            "restart_policy": self._extract_restart_policy(attrs_dict.get("HostConfig")),
            "ports": self._extract_ports(attrs_dict.get("NetworkSettings")),
            "network_names": [
                network["name"]
                for network in self._extract_networks(attrs_dict.get("NetworkSettings"))
            ],
            "mounts": self._extract_mounts(attrs_dict.get("Mounts")),
            "env_var_names": self._extract_env_var_names(config.get("Env")),
            "command_preview": self._build_command_preview(
                self._extract_string_list(config.get("Cmd"))
            ),
            "triage_notes": self._build_container_triage_notes(health),
        }

    @staticmethod
    def _parse_volume_inventory(attrs: object) -> dict[str, Any]:
        attrs_dict = attrs if isinstance(attrs, dict) else {}
        labels = attrs_dict.get("Labels") if isinstance(attrs_dict.get("Labels"), dict) else {}
        options = attrs_dict.get("Options") if isinstance(attrs_dict.get("Options"), dict) else {}
        usage_data = (
            attrs_dict.get("UsageData") if isinstance(attrs_dict.get("UsageData"), dict) else {}
        )
        return {
            "volume_name": str(attrs_dict.get("Name") or ""),
            "driver": DockerSdkAdapter._optional_string(attrs_dict.get("Driver")),
            "scope": DockerSdkAdapter._optional_string(attrs_dict.get("Scope")),
            "created_at": DockerSdkAdapter._optional_string(attrs_dict.get("CreatedAt")),
            "compose_labels": {
                str(key): str(value)
                for key, value in labels.items()
                if key in SAFE_COMPOSE_LABEL_KEYS and value is not None
            },
            "option_keys": sorted(str(key) for key in options),
            "mountpoint_available": bool(attrs_dict.get("Mountpoint")),
            "mountpoint_redacted": bool(attrs_dict.get("Mountpoint")),
            "usage_ref_count": usage_data.get("RefCount"),
            "usage_size_bytes": usage_data.get("Size"),
        }

    @staticmethod
    def _parse_container_health(attrs: object, container_name: str) -> dict[str, Any]:
        attrs_dict = attrs if isinstance(attrs, dict) else {}
        state = attrs_dict.get("State") if isinstance(attrs_dict.get("State"), dict) else {}
        config = attrs_dict.get("Config") if isinstance(attrs_dict.get("Config"), dict) else {}
        health = state.get("Health") if isinstance(state.get("Health"), dict) else {}
        raw_name = attrs_dict.get("Name")
        raw_exit_code = state.get("ExitCode")
        raw_restart_count = attrs_dict.get("RestartCount")
        raw_error = state.get("Error")
        running = bool(state.get("Running", False))
        return {
            "container_id": str(attrs_dict.get("Id", "")),
            "container_name": str(raw_name).lstrip("/") if raw_name else container_name,
            "image": DockerSdkAdapter._optional_string(config.get("Image")),
            "docker_status": DockerSdkAdapter._optional_string(state.get("Status")),
            "health_status": DockerSdkAdapter._optional_string(health.get("Status")),
            "running": running,
            "restarting": bool(state.get("Restarting", False)),
            "paused": bool(state.get("Paused", False)),
            "dead": bool(state.get("Dead", False)),
            "exit_code": raw_exit_code if isinstance(raw_exit_code, int) else None,
            "error": str(raw_error) if raw_error not in {None, ""} else "",
            "restart_count": raw_restart_count if isinstance(raw_restart_count, int) else None,
            "started_at": DockerSdkAdapter._optional_string(state.get("StartedAt")),
            "finished_at": (
                DockerSdkAdapter._optional_string(state.get("FinishedAt")) if not running else None
            ),
        }

    @staticmethod
    def _extract_env_var_names(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        names = []
        for item in value:
            if isinstance(item, str):
                names.append(item.split("=", 1)[0])
        return sorted(names)

    @staticmethod
    def _extract_env_vars(value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        safe_value_names = {"APP_ENV", "ENV", "ENVIRONMENT", "LOG_LEVEL", "NODE_ENV", "PORT"}
        results = []
        for item in value:
            if not isinstance(item, str) or "=" not in item:
                continue
            name, raw_value = item.split("=", 1)
            expose_value = name in safe_value_names
            results.append(
                {
                    "name": name,
                    "value": raw_value if expose_value else None,
                    "value_redacted": not expose_value,
                    "secret": any(
                        part in name.upper() for part in ("SECRET", "TOKEN", "PASSWORD", "KEY")
                    ),
                }
            )
        return results

    @staticmethod
    def _extract_compose_labels(value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {
            str(key): str(label_value)
            for key, label_value in value.items()
            if key in SAFE_COMPOSE_LABEL_KEYS and label_value is not None
        }

    @staticmethod
    def _extract_string_list(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if item is not None]
        if isinstance(value, str) and value:
            return [value]
        return []

    @staticmethod
    def _extract_ports(value: object) -> list[dict[str, Any]]:
        network_settings = value if isinstance(value, dict) else {}
        ports = (
            network_settings.get("Ports") if isinstance(network_settings.get("Ports"), dict) else {}
        )
        results = []
        for private_port, bindings in ports.items():
            if not isinstance(bindings, list):
                continue
            for binding in bindings:
                if not isinstance(binding, dict):
                    continue
                results.append(
                    {
                        "private_port": str(private_port),
                        "host_ip": DockerSdkAdapter._optional_string(binding.get("HostIp")),
                        "host_port": DockerSdkAdapter._optional_string(binding.get("HostPort")),
                    }
                )
        return results

    @staticmethod
    def _extract_mounts(value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        results = []
        for item in value:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "type": DockerSdkAdapter._optional_string(item.get("Type")),
                    "destination": DockerSdkAdapter._optional_string(item.get("Destination")),
                    "mode": DockerSdkAdapter._optional_string(item.get("Mode")),
                    "rw": item.get("RW") if isinstance(item.get("RW"), bool) else None,
                    "name": DockerSdkAdapter._optional_string(item.get("Name")),
                }
            )
        return results

    @staticmethod
    def _extract_restart_policy(value: object) -> dict[str, Any]:
        host_config = value if isinstance(value, dict) else {}
        restart_policy = (
            host_config.get("RestartPolicy")
            if isinstance(host_config.get("RestartPolicy"), dict)
            else {}
        )
        return {
            "name": DockerSdkAdapter._optional_string(restart_policy.get("Name")),
            "maximum_retry_count": (
                restart_policy.get("MaximumRetryCount")
                if isinstance(restart_policy.get("MaximumRetryCount"), int)
                else None
            ),
        }

    @staticmethod
    def _extract_networks(value: object) -> list[dict[str, Any]]:
        network_settings = value if isinstance(value, dict) else {}
        networks = (
            network_settings.get("Networks")
            if isinstance(network_settings.get("Networks"), dict)
            else {}
        )
        results = []
        for name, metadata in networks.items():
            metadata_dict = metadata if isinstance(metadata, dict) else {}
            aliases = metadata_dict.get("Aliases")
            results.append(
                {
                    "name": str(name),
                    "ip_address": DockerSdkAdapter._optional_string(metadata_dict.get("IPAddress")),
                    "aliases": (
                        [str(alias) for alias in aliases] if isinstance(aliases, list) else []
                    ),
                }
            )
        return results

    @staticmethod
    def _optional_string(value: object) -> str | None:
        if value is None or value == "":
            return None
        return str(value)

    @staticmethod
    def _build_command_preview(command: list[str]) -> str:
        preview = " ".join(command)
        return preview if len(preview) <= 240 else f"{preview[:240].rstrip()}..."

    @staticmethod
    def _build_container_triage_notes(health: dict[str, Any]) -> list[str]:
        notes: list[str] = []
        if not health.get("running"):
            notes.append("not_running")
        if health.get("restarting"):
            notes.append("restarting")
        if health.get("paused"):
            notes.append("paused")
        if health.get("dead"):
            notes.append("dead")
        health_status = health.get("health_status")
        if health_status not in {None, "healthy"}:
            notes.append(f"health_status={health_status}")
        exit_code = health.get("exit_code")
        if exit_code not in {None, 0}:
            notes.append(f"exit_code={exit_code}")
        restart_count = health.get("restart_count")
        if isinstance(restart_count, int) and restart_count >= 5:
            notes.append(f"restart_count={restart_count}")
        return notes

    @staticmethod
    def _extract_health_log(value: object) -> list[dict[str, Any]]:
        state = value if isinstance(value, dict) else {}
        health = state.get("Health") if isinstance(state.get("Health"), dict) else {}
        raw_entries = health.get("Log") if isinstance(health.get("Log"), list) else []
        entries = []
        for item in raw_entries[-5:]:
            if not isinstance(item, dict):
                continue
            output = item.get("Output")
            entries.append(
                {
                    "start": item.get("Start"),
                    "end": item.get("End"),
                    "exit_code": item.get("ExitCode"),
                    "output": str(output)[:4000] if output is not None else None,
                }
            )
        return entries
