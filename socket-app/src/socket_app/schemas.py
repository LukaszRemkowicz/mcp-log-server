"""Shared contracts and JSON-compatible shapes for the generic socket app."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal, TypedDict


class ErrorPayload(TypedDict):
    """JSON error payload returned to socket clients."""

    message: str


class ErrorResponse(TypedDict):
    """JSON response returned when request validation or execution fails."""

    ok: Literal[False]
    error: ErrorPayload


class SuccessResponse(TypedDict):
    """JSON response returned when a fixed socket operation succeeds."""

    ok: Literal[True]
    result: dict[str, Any]


SocketResponse = SuccessResponse | ErrorResponse


class DockerBackend(ABC):
    """Abstract operation backend used by the operation registry.

    `SocketOperationRegistry` owns request-level validation and operation
    routing. The backend owns Docker-backed implementation details. Keeping
    this contract explicit makes the socket app small: clients can request
    only these methods, not arbitrary Docker API calls, shell commands, or
    mutation operations.

    Implementations should return JSON-serializable dictionaries and raise
    regular exceptions for expected Docker/runtime failures. The Unix-socket
    server converts those exceptions into `{"ok": false, ...}` responses.
    """

    @abstractmethod
    def container_logs(
        self,
        *,
        container_name: str,
        since: str | None = None,
        until: str | None = None,
        tail: int | None = None,
    ) -> dict[str, Any]:
        """Return bounded timestamped logs for one container.

        `since`, `until`, and `tail` are passed as Docker log filters. The
        implementation must not follow logs forever and should cap the response
        size so a single request cannot exhaust memory.
        """

    @abstractmethod
    def container_health(self, *, container_name: str) -> dict[str, Any]:
        """Return bounded runtime state for one container."""

    @abstractmethod
    def container_detail(self, *, container_name: str) -> dict[str, Any]:
        """Return sanitized inspect-style metadata for one container."""

    @abstractmethod
    def container_path_stat(self, *, container_name: str, path: str) -> dict[str, Any]:
        """Return file or directory metadata for one absolute container path."""

    @abstractmethod
    def container_file_read(
        self,
        *,
        container_name: str,
        path: str,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Read a bounded regular file inside a container.

        The backend should reject directories, invalid paths, and non-positive
        byte limits. The caller chooses the path, but the backend still performs
        local normalization before touching Docker.
        """

    @abstractmethod
    def container_directory_list(
        self,
        *,
        container_name: str,
        path: str,
        max_entries: int | None = None,
    ) -> dict[str, Any]:
        """List one bounded directory or return one file metadata entry."""

    @abstractmethod
    def vps_containers_inventory(self) -> dict[str, Any]:
        """Return bounded and redacted container inventory."""

    @abstractmethod
    def vps_volumes_inventory(
        self,
        *,
        dangling_only: bool = False,
        anonymous_only: bool = False,
        name_prefix: str | None = None,
    ) -> dict[str, Any]:
        """Return bounded and redacted Docker volume inventory."""

    @abstractmethod
    def traefik_router_tls_inventory(self) -> dict[str, Any]:
        """Return bounded and sanitized Traefik router TLS inventory."""

    @abstractmethod
    def crowdsec_activity(self, *, container_name: str) -> dict[str, Any]:
        """Return fixed read-only CrowdSec diagnostics from one container."""

    @abstractmethod
    def landingpage_django_list_commands(
        self, *, container_name: str, base_command: list[str], cwd: str
    ) -> dict[str, Any]:
        """Return available fixed landingpage Django command metadata."""

    @abstractmethod
    def landingpage_django_media_inventory(
        self, *, container_name: str, base_command: list[str], cwd: str
    ) -> dict[str, Any]:
        """Return landingpage media inventory from the fixed Django command."""
