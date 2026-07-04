from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from socket_app import DockerSocketServer, SocketOperationRegistry


class FakeDockerBackend:
    def container_health(self, *, container_name: str) -> dict[str, Any]:
        return {"container_name": container_name, "running": True}

    def crowdsec_activity(self, *, container_name: str) -> dict[str, Any]:
        return {"container_name": container_name, "sections": {}}


@pytest.mark.asyncio
async def test_unix_socket_server_handles_one_json_line_request() -> None:
    socket_path = Path("/tmp") / f"socket-app-{uuid4().hex}.sock"
    operation_registry = SocketOperationRegistry(backend=FakeDockerBackend())
    server = DockerSocketServer(
        socket_path=socket_path,
        operation_registry=operation_registry,
    )

    async with server.running():
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        writer.write(
            b'{"operation":"container_health","params":{"container_name":"portfolio-prod-be-1"}}\n'
        )
        await writer.drain()

        raw_response = await reader.readline()
        writer.close()
        await writer.wait_closed()

    assert raw_response == (
        b'{"ok":true,"result":{"container_name":"portfolio-prod-be-1","running":true}}\n'
    )
