from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import pytest

from fail2ban_socket_app import Fail2banSocketServer, Fail2banSocketService


class FakeFail2banBackend:
    def get_jail_bans(self, *, jail_name: str) -> dict[str, Any]:
        return {"jail_name": jail_name, "currently_banned": 1}


@pytest.mark.asyncio
async def test_unix_socket_server_handles_one_json_line_request() -> None:
    base_dir = Path("/private/tmp") if Path("/private/tmp").exists() else None
    with tempfile.TemporaryDirectory(prefix="f2b-", dir=base_dir) as tmp_dir:
        socket_path = Path(tmp_dir) / "app.sock"
        raw_response = await _request_jail_status(socket_path)

    assert raw_response == (
        b'{"ok":true,"result":{"jail_name":"portfolio-nginx-probes","currently_banned":1}}\n'
    )


@pytest.mark.asyncio
async def test_unix_socket_server_allows_mcp_app_connection_when_running_as_root() -> None:
    base_dir = Path("/private/tmp") if Path("/private/tmp").exists() else None
    with tempfile.TemporaryDirectory(prefix="f2b-", dir=base_dir) as tmp_dir:
        socket_path = Path(tmp_dir) / "app.sock"
        service = Fail2banSocketService(backend=FakeFail2banBackend())
        server = Fail2banSocketServer(socket_path=socket_path, service=service)

        async with server.running():
            socket_mode = socket_path.stat().st_mode & 0o777

    assert socket_mode == 0o666


async def _request_jail_status(socket_path: Path) -> bytes:
    service = Fail2banSocketService(backend=FakeFail2banBackend())
    server = Fail2banSocketServer(socket_path=socket_path, service=service)

    async with server.running():
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        writer.write(
            b'{"operation":"get_jail_bans","params":{"jail_name":"portfolio-nginx-probes"}}\n'
        )
        await writer.drain()

        raw_response = await reader.readline()
        writer.close()
        await writer.wait_closed()

    return raw_response
