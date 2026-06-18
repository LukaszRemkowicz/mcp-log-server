"""Entrypoint for the fail2ban socket app."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .adapters import Fail2banClientAdapter
from .server import Fail2banSocketServer
from .services import Fail2banSocketService


def main() -> None:
    """Start the Unix-socket fail2ban diagnostics app."""

    socket_path = Path(os.environ["FAIL2BAN_SOCKET_APP_SOCKET_PATH"])
    fail2ban_socket_path = os.environ["FAIL2BAN_SOCKET_PATH"]
    service = Fail2banSocketService(backend=Fail2banClientAdapter(socket_path=fail2ban_socket_path))
    server = Fail2banSocketServer(socket_path=socket_path, service=service)
    try:
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
