"""Docker SDK adapter for the generic socket app."""

from __future__ import annotations

import re
import tempfile
from base64 import b64encode
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import RLock
from time import monotonic
from time import time as wall_time
from typing import Any, Literal
from uuid import uuid4

import environ
from docker import from_env
from docker.errors import DockerException
from requests import exceptions as requests_exceptions

from . import settings
from .exceptions import DockerBackendError
from .schemas import DockerBackend
from .services import BackendCommandRunService, CrowdSecService, DockerService

ANONYMOUS_VOLUME_NAME_PATTERN = re.compile(settings.ANONYMOUS_VOLUME_NAME_PATTERN)


@dataclass(slots=True)
class _LogTransfer:
    transfer_id: str
    container_name: str
    spool_path: Path
    byte_count: int
    next_offset: int
    last_accessed_at: float


@dataclass(frozen=True, slots=True)
class LogTransferPage:
    transfer_id: str | None
    container_name: str
    content: bytes
    offset: int
    byte_limit: int
    truncated: bool
    next_offset: int | None

    @property
    def returned_bytes(self) -> int:
        return len(self.content)

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-serializable socket response payload."""

        return {
            "transfer_id": self.transfer_id,
            "container_name": self.container_name,
            "logs_base64": b64encode(self.content).decode("ascii"),
            "offset": self.offset,
            "returned_bytes": self.returned_bytes,
            "byte_limit": self.byte_limit,
            "truncated": self.truncated,
            "next_offset": self.next_offset,
        }


class LogTransferSpool:
    """Own temporary Docker-log transfer files and paging cursors."""

    def __init__(
        self,
        *,
        directory: Path,
        ttl_seconds: float,
        max_bytes: int,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self.max_bytes = max_bytes
        if self.max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer.")
        self._clock = clock
        self._transfers: dict[str, _LogTransfer] = {}
        self._lock = RLock()
        self._cleanup_orphan_files()

    @classmethod
    def from_settings(cls) -> LogTransferSpool:
        """Build the default spool from socket-app runtime settings."""

        return cls(
            directory=settings.LOG_TRANSFER_DIR,
            ttl_seconds=settings.LOG_TRANSFER_TTL_SECONDS,
            max_bytes=settings.MAX_LOG_TRANSFER_BYTES,
        )

    def create(self, *, container_name: str, chunks: Any) -> _LogTransfer:
        """Create one immutable spool file from Docker log chunks."""

        spool_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix="log-transfer-",
                suffix=".part",
                dir=self.directory,
                delete=False,
            ) as spool:
                spool_path = Path(spool.name)
                byte_count = 0
                for chunk in chunks:
                    raw = chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8")
                    if byte_count + len(raw) > self.max_bytes:
                        raise DockerBackendError(
                            f"Docker log transfer exceeded maximum size of {self.max_bytes} bytes."
                        )
                    spool.write(raw)
                    byte_count += len(raw)
        except Exception:
            if spool_path is not None:
                spool_path.unlink(missing_ok=True)
            raise

        assert spool_path is not None
        final_spool_path = spool_path.with_suffix(".spool")
        spool_path.replace(final_spool_path)
        transfer = _LogTransfer(
            transfer_id=uuid4().hex,
            container_name=container_name,
            spool_path=final_spool_path,
            byte_count=byte_count,
            next_offset=0,
            last_accessed_at=self._clock(),
        )
        self._transfers[transfer.transfer_id] = transfer
        return transfer

    def create_page(
        self,
        *,
        transfer: _LogTransfer,
        offset: int,
        byte_limit: int,
    ) -> LogTransferPage:
        """Return the first page from a newly-created spool."""

        with self._lock:
            self._cleanup_expired_transfers()
            return self._page_from_transfer(transfer=transfer, offset=offset, byte_limit=byte_limit)

    def read_page(
        self,
        *,
        transfer_id: str,
        offset: int,
        byte_limit: int,
    ) -> LogTransferPage:
        """Return one page from an existing spool."""

        with self._lock:
            self._cleanup_expired_transfers()
            transfer = self._transfers.get(transfer_id)
            if transfer is None:
                raise DockerBackendError("Unknown or expired log transfer id.")
            if offset != transfer.next_offset:
                raise DockerBackendError(f"Log transfer offset must be {transfer.next_offset}.")
            return self._page_from_transfer(transfer=transfer, offset=offset, byte_limit=byte_limit)

    def _page_from_transfer(
        self,
        *,
        transfer: _LogTransfer,
        offset: int,
        byte_limit: int,
    ) -> LogTransferPage:
        with transfer.spool_path.open("rb") as spool:
            spool.seek(offset)
            page = spool.read(byte_limit)
        next_offset = offset + len(page)
        truncated = next_offset < transfer.byte_count
        if truncated:
            transfer.next_offset = next_offset
            transfer.last_accessed_at = self._clock()
            response_transfer_id: str | None = transfer.transfer_id
        else:
            self.delete(transfer.transfer_id)
            response_transfer_id = None

        return LogTransferPage(
            transfer_id=response_transfer_id,
            container_name=transfer.container_name,
            content=page,
            offset=offset,
            byte_limit=byte_limit,
            truncated=truncated,
            next_offset=next_offset if truncated else None,
        )

    def _cleanup_expired_transfers(self) -> None:
        expires_before = self._clock() - self.ttl_seconds
        expired_ids = [
            transfer_id
            for transfer_id, transfer in self._transfers.items()
            if transfer.last_accessed_at <= expires_before
        ]
        for transfer_id in expired_ids:
            self.delete(transfer_id)
        self._cleanup_orphan_files()

    def _cleanup_orphan_files(self) -> None:
        expires_before = wall_time() - self.ttl_seconds
        active_paths = {transfer.spool_path for transfer in self._transfers.values()}
        for pattern in ("log-transfer-*.spool", "log-transfer-*.part"):
            for spool_path in self.directory.glob(pattern):
                if spool_path in active_paths:
                    continue
                try:
                    if spool_path.stat().st_mtime <= expires_before:
                        spool_path.unlink(missing_ok=True)
                except FileNotFoundError:
                    continue

    def delete(self, transfer_id: str) -> None:
        transfer = self._transfers.pop(transfer_id, None)
        if transfer is not None:
            transfer.spool_path.unlink(missing_ok=True)


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

    def __init__(
        self,
        client: Any | None = None,
        *,
        log_transfer_spool: LogTransferSpool | None = None,
    ) -> None:
        self.client = client or from_env(timeout=settings.DOCKER_TIMEOUT_SECONDS)
        self.log_transfer_spool = log_transfer_spool or LogTransferSpool.from_settings()
        self.docker_service = DockerService(self.client)
        self.crowdsec_service = CrowdSecService(self.docker_service)
        self.backend_command_run_service = BackendCommandRunService(self.docker_service)

    def service_health(self) -> dict[str, Any]:
        """Return healthy only when the Docker daemon answers a SDK ping."""

        try:
            docker_reachable = self.client.ping() is True
        except requests_exceptions.Timeout as error:
            raise DockerBackendError("Timed out pinging the Docker daemon.") from error
        except DockerException as error:
            raise DockerBackendError(str(error).strip() or "Docker daemon ping failed.") from error
        if not docker_reachable:
            raise DockerBackendError("Docker daemon ping failed.")
        return {"status": "ok", "docker_reachable": True}

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
            if total_bytes > settings.MAX_LOG_BYTES:
                truncated = True
                break
            lines.append(raw.decode("utf-8", errors="replace").rstrip("\n"))
        return {"container_name": container_name, "logs": lines, "truncated": truncated}

    def container_logs_page(
        self,
        *,
        transfer_id: str | None = None,
        container_name: str | None = None,
        stream: Literal["stdout", "stderr"] | None = None,
        since: str | None = None,
        until: str | None = None,
        tail: int | None = None,
        offset: int = 0,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Page one immutable Docker-log spool through an opaque cursor."""

        if offset < 0:
            raise DockerBackendError("offset must be a non-negative integer.")
        if max_bytes is not None and max_bytes < 1:
            raise DockerBackendError("max_bytes must be a positive integer.")
        byte_limit = min(max_bytes or settings.MAX_LOG_BYTES, settings.MAX_LOG_BYTES)

        page: LogTransferPage
        if transfer_id is None:
            if offset != 0:
                raise DockerBackendError("A new log transfer must start at offset 0.")
            if not container_name:
                raise DockerBackendError("container_name is required for a new log transfer.")
            transfer: _LogTransfer = self._create_log_transfer(
                container_name=container_name,
                stream=stream,
                since=since,
                until=until,
                tail=tail,
            )
            page = self.log_transfer_spool.create_page(
                transfer=transfer,
                offset=offset,
                byte_limit=byte_limit,
            )
        else:
            if any(value is not None for value in (container_name, stream, since, until, tail)):
                raise DockerBackendError(
                    "A continued log transfer accepts only transfer_id, offset, and max_bytes."
                )
            page = self.log_transfer_spool.read_page(
                transfer_id=transfer_id,
                offset=offset,
                byte_limit=byte_limit,
            )

        return page.to_payload()

    def _create_log_transfer(
        self,
        *,
        container_name: str,
        stream: Literal["stdout", "stderr"] | None,
        since: str | None,
        until: str | None,
        tail: int | None,
    ) -> _LogTransfer:
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
                stdout=stream != "stderr",
                stderr=stream != "stdout",
                stream=True,
                **kwargs,
            )
            return self.log_transfer_spool.create(container_name=container_name, chunks=chunks)
        except requests_exceptions.Timeout as error:
            raise DockerBackendError(
                f"Timed out collecting docker logs for {container_name}."
            ) from error
        except DockerException as error:
            raise DockerBackendError(str(error).strip() or "Docker operation failed.") from error

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

        container = self.docker_service.get_container(container_name)
        return self._parse_container_health(container.attrs, container_name)

    def container_detail(self, *, container_name: str) -> dict[str, Any]:
        """Return curated inspect-style metadata for one container."""

        container = self.docker_service.get_container(container_name)
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
        output = self.docker_service.run_text(
            container_name=container_name,
            command=[
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

        limit = max_bytes or settings.MAX_FILE_BYTES
        if limit < 1:
            raise DockerBackendError("max_bytes must be a positive integer.")
        path_stat = self.container_path_stat(container_name=container_name, path=path)
        if path_stat["is_dir"]:
            raise DockerBackendError(
                "Requested container path is a directory, not a readable regular file."
            )
        normalized_path = self._normalize_container_path(path)
        output = self.docker_service.run_text(
            container_name=container_name,
            command=[
                "find",
                normalized_path,
                "-maxdepth",
                "0",
                "-type",
                "f",
                "-exec",
                "cat",
                "{}",
                ";",
            ],
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

        limit = max_entries or settings.MAX_DIRECTORY_ENTRIES
        path_stat = self.container_path_stat(container_name=container_name, path=path)
        if not path_stat["is_dir"]:
            return {"path": path_stat["path"], "entries": [path_stat], "truncated": False}

        output = self.docker_service.run_text(
            container_name=container_name,
            command=[
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
            "containers": rows[: settings.MAX_VPS_CONTAINERS],
            "truncated": len(rows) > settings.MAX_VPS_CONTAINERS,
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
        return {
            "volumes": rows[: settings.MAX_VPS_VOLUMES],
            "truncated": len(rows) > settings.MAX_VPS_VOLUMES,
        }

    def traefik_router_tls_inventory(self) -> dict[str, Any]:
        """Return bounded, sanitized Traefik HTTP router TLS inventory."""

        try:
            containers = self.client.containers.list(all=True)
        except requests_exceptions.Timeout as error:
            raise DockerBackendError("Timed out listing Docker containers.") from error
        except DockerException as error:
            raise DockerBackendError(str(error).strip() or "Docker operation failed.") from error

        rows: list[dict[str, Any]] = []
        for container in containers:
            rows.extend(self._extract_traefik_router_tls_rows(container.attrs))
        rows.sort(key=lambda item: (item["router_name"], item["container_name"]))
        return {
            "routers": rows[: settings.MAX_TRAEFIK_ROUTERS],
            "truncated": len(rows) > settings.MAX_TRAEFIK_ROUTERS,
        }

    def crowdsec_activity(self, *, container_name: str) -> dict[str, Any]:
        """Return fixed read-only CrowdSec diagnostics from one container."""

        return self.crowdsec_service.inspect_activity(container_name=container_name)

    def landingpage_django_list_commands(
        self, *, container_name: str, base_command: list[str], cwd: str
    ) -> dict[str, Any]:
        """Return available fixed landingpage Django command metadata."""

        return self.backend_command_run_service.list_landingpage_commands(
            container_name=container_name,
            base_command=base_command,
            cwd=cwd,
        )

    def landingpage_django_media_inventory(
        self, *, container_name: str, base_command: list[str], cwd: str
    ) -> dict[str, Any]:
        """Return landingpage media inventory from the fixed Django command."""

        return self.backend_command_run_service.inspect_landingpage_media_inventory(
            container_name=container_name,
            base_command=base_command,
            cwd=cwd,
        )

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
                if key in settings.SAFE_COMPOSE_LABEL_KEYS and value is not None
            },
            "option_keys": sorted(str(key) for key in options),
            "mountpoint_available": bool(attrs_dict.get("Mountpoint")),
            "mountpoint_redacted": bool(attrs_dict.get("Mountpoint")),
            "usage_ref_count": usage_data.get("RefCount"),
            "usage_size_bytes": usage_data.get("Size"),
        }

    @classmethod
    def _extract_traefik_router_tls_rows(cls, attrs: object) -> list[dict[str, Any]]:
        attrs_dict = attrs if isinstance(attrs, dict) else {}
        config = attrs_dict.get("Config") if isinstance(attrs_dict.get("Config"), dict) else {}
        labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
        if str(labels.get("traefik.enable", "")).strip().lower() != "true":
            return []

        routers: dict[str, dict[str, str]] = {}
        for raw_key, raw_value in labels.items():
            if raw_value is None:
                continue
            key = str(raw_key)
            if not key.startswith(settings.TRAEFIK_ROUTER_LABEL_PREFIX):
                continue
            remainder = key.removeprefix(settings.TRAEFIK_ROUTER_LABEL_PREFIX)
            router_name, separator, property_name = remainder.partition(".")
            if (
                not separator
                or not router_name
                or property_name not in settings.TRAEFIK_ROUTER_SAFE_PROPERTIES
            ):
                continue
            routers.setdefault(router_name, {})[property_name] = str(raw_value)

        raw_container_name = attrs_dict.get("Name")
        container_name = str(raw_container_name).lstrip("/") if raw_container_name else ""
        rows: list[dict[str, Any]] = []
        for router_name, properties in routers.items():
            tls_enabled = cls._parse_traefik_tls_enabled(properties)
            cert_resolver = cls._optional_string(properties.get("tls.certresolver"))
            rows.append(
                {
                    "router_name": router_name,
                    "container_name": container_name,
                    "rule": cls._optional_string(properties.get("rule")),
                    "entrypoints": cls._split_traefik_entrypoints(properties.get("entrypoints")),
                    "service": cls._optional_string(properties.get("service")),
                    "tls_enabled": tls_enabled,
                    "cert_resolver": cert_resolver,
                    "certificate_source": cls._derive_traefik_certificate_source(
                        tls_enabled=tls_enabled,
                        cert_resolver=cert_resolver,
                    ),
                }
            )
        return rows

    @staticmethod
    def _split_traefik_entrypoints(value: object) -> list[str]:
        if value is None:
            return []
        return [part.strip() for part in str(value).split(",") if part.strip()]

    @staticmethod
    def _parse_traefik_tls_enabled(properties: dict[str, str]) -> bool:
        if properties.get("tls.certresolver"):
            return True
        tls_value = properties.get("tls")
        if tls_value is None:
            return False
        return bool(environ.Env.parse_value(tls_value, bool))

    @staticmethod
    def _derive_traefik_certificate_source(
        *,
        tls_enabled: bool,
        cert_resolver: str | None,
    ) -> str:
        if not tls_enabled:
            return "not_tls"
        if cert_resolver:
            return "acme_resolver"
        return "static_or_default"

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
        results = []
        for item in value:
            if not isinstance(item, str) or "=" not in item:
                continue
            name, raw_value = item.split("=", 1)
            secret = any(part in name.upper() for part in settings.SECRET_ENV_NAME_PARTS)
            expose_value = not secret and name in settings.SAFE_ENV_VALUE_NAMES
            results.append(
                {
                    "name": name,
                    "value": raw_value if expose_value else None,
                    "value_redacted": not expose_value,
                    "secret": secret,
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
            if key in settings.SAFE_COMPOSE_LABEL_KEYS and label_value is not None
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
