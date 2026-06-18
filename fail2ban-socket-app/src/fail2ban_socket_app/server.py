"""Unix-domain socket server for the fail2ban socket app."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

from .dispatcher import dispatch_request
from .exceptions import ProtocolException
from .services import Fail2banSocketService


class Fail2banSocketServer:
    """Serve one JSON request per line over a Unix-domain socket."""

    def __init__(self, *, socket_path: Path, service: Fail2banSocketService) -> None:
        self.socket_path = socket_path
        self.service = service
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
        os.chmod(self.socket_path, 0o666)

    async def stop(self) -> None:
        """Stop listening and remove the socket file."""

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self.socket_path.exists():
            self.socket_path.unlink()

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
                raise RuntimeError("Fail2ban socket server did not start.")
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
                writer.write(self._build_response(raw_line))
                with suppress(BrokenPipeError, ConnectionResetError):
                    await writer.drain()
        finally:
            with suppress(BrokenPipeError, ConnectionResetError):
                writer.close()
                await writer.wait_closed()

    def _build_response(self, raw_line: bytes) -> bytes:
        try:
            decoded = json.loads(raw_line.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ProtocolException("Request must be a JSON object.")
            response = dispatch_request(decoded, self.service)
        except (json.JSONDecodeError, ProtocolException) as error:
            response = {"ok": False, "error": {"message": str(error)}}
        except Exception as error:  # pragma: no cover - defensive service boundary
            response = {
                "ok": False,
                "error": {"message": str(error) or "Fail2ban operation failed."},
            }
        return json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"
