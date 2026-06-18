from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from fail2ban_socket_app import Fail2banSocketServer, Fail2banSocketService


class FakeFail2banBackend:
    def get_jail_bans(self, *, jail_name: str) -> dict[str, Any]:
        return {"jail_name": jail_name, "currently_banned": 1}


@pytest.mark.asyncio
async def test_unix_socket_server_handles_one_json_line_request() -> None:
    socket_path = Path("/tmp") / f"fail2ban-socket-app-{uuid4().hex}.sock"
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

    assert raw_response == (
        b'{"ok":true,"result":{"jail_name":"portfolio-nginx-probes","currently_banned":1}}\n'
    )
