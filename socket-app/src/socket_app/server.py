"""Unix-domain socket server for the generic socket app."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from time import perf_counter

from .dispatcher import dispatch_request
from .exceptions import DockerBackendError, ProtocolException
from .services import SocketOperationRegistry

logger = logging.getLogger("socket_app.server")

_SUPPORTED_OPERATIONS = frozenset(
    {
        "service_health",
        "container_logs",
        "container_logs_page",
        "container_health",
        "container_detail",
        "container_path_stat",
        "container_file_read",
        "container_directory_list",
        "vps_containers_inventory",
        "vps_volumes_inventory",
        "traefik_router_tls_inventory",
        "crowdsec_activity",
        "landingpage_django_list_commands",
        "landingpage_django_media_inventory",
    }
)
_UNSUPPORTED_OPERATION_LABEL = "unsupported"
_INVALID_OPERATION_LABEL = "invalid"


class DockerSocketServer:
    """Serve one JSON request per line over a Unix-domain socket."""

    def __init__(
        self,
        *,
        socket_path: Path,
        operation_registry: SocketOperationRegistry,
    ) -> None:
        self.socket_path = socket_path
        self.operation_registry = operation_registry
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        """Start listening on the configured Unix socket path."""

        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )
        os.chmod(self.socket_path, 0o660)
        logger.info(
            "socket_server_started",
            extra={"socket_path": str(self.socket_path)},
        )

    async def stop(self) -> None:
        """Stop listening and remove the socket file."""

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self.socket_path.exists():
            self.socket_path.unlink()
        logger.info(
            "socket_server_stopped",
            extra={"socket_path": str(self.socket_path)},
        )

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        """Run this server inside an async context manager."""

        await self.start()
        try:
            yield
        finally:
            await self.stop()

    async def serve_forever(self) -> None:
        """Serve requests until cancelled."""

        await self.start()
        try:
            if self._server is None:
                raise RuntimeError("Docker socket server did not start.")
            await self._server.serve_forever()
        finally:
            await self.stop()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while raw_line := await reader.readline():
                writer.write(await asyncio.to_thread(self._build_response, raw_line))
                with suppress(BrokenPipeError, ConnectionResetError):
                    await writer.drain()
        finally:
            with suppress(BrokenPipeError, ConnectionResetError):
                writer.close()
                await writer.wait_closed()

    def _build_response(self, raw_line: bytes) -> bytes:
        started_at = perf_counter()
        operation = _INVALID_OPERATION_LABEL
        unsupported_operation = False
        request_error: Exception | None = None
        try:
            decoded = json.loads(raw_line.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ProtocolException("Request must be a JSON object.")
            raw_operation = decoded.get("operation")
            operation, unsupported_operation = self._operation_log_label(raw_operation)
            response = dispatch_request(decoded, self.operation_registry)
        except (json.JSONDecodeError, ProtocolException) as error:
            request_error = error
            response = {"ok": False, "error": {"message": str(error)}}
        except Exception as error:  # pragma: no cover - defensive service boundary
            request_error = error
            response = {"ok": False, "error": {"message": str(error) or "Docker operation failed."}}
        self._log_request_outcome(
            operation=operation,
            unsupported_operation=unsupported_operation,
            ok=response["ok"],
            request_error=request_error,
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
        )
        return json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"

    @staticmethod
    def _operation_log_label(raw_operation: object) -> tuple[str, bool]:
        """Return only a fixed recognized operation name or bounded sentinel."""

        if isinstance(raw_operation, str) and raw_operation in _SUPPORTED_OPERATIONS:
            return raw_operation, False
        if isinstance(raw_operation, str):
            return _UNSUPPORTED_OPERATION_LABEL, True
        return _INVALID_OPERATION_LABEL, False

    @staticmethod
    def _log_request_outcome(
        *,
        operation: str,
        unsupported_operation: bool,
        ok: bool,
        request_error: Exception | None,
        duration_ms: float,
    ) -> None:
        """Log bounded request metadata at a level useful to MCP analysis."""

        operation_label, derived_unsupported_operation = DockerSocketServer._operation_log_label(
            operation
        )
        unsupported_operation = unsupported_operation or derived_unsupported_operation
        fields: dict[str, object] = {
            "operation": operation_label,
            "ok": ok,
            "duration_ms": duration_ms,
        }
        if not ok:
            error_category, error_code = DockerSocketServer._error_log_classification(
                request_error=request_error,
                unsupported_operation=unsupported_operation,
            )
            fields.update(
                {
                    "error_category": error_category,
                    "error_code": error_code,
                }
            )
            logger.error("socket_request_completed", extra=fields)
            return
        if operation_label == "service_health":
            logger.debug("socket_request_completed", extra=fields)
            return
        logger.info("socket_request_completed", extra=fields)

    @staticmethod
    def _error_log_classification(
        *,
        request_error: Exception | None,
        unsupported_operation: bool,
    ) -> tuple[str, str]:
        """Map request failures to stable categories without logging error text."""

        if isinstance(request_error, json.JSONDecodeError):
            return "protocol", "invalid_json"
        if isinstance(request_error, ProtocolException):
            if unsupported_operation:
                return "protocol", "unsupported_operation"
            return "protocol", "invalid_request"
        if isinstance(request_error, DockerBackendError):
            return "docker_backend", "docker_backend_error"
        return "internal", "internal_error"
