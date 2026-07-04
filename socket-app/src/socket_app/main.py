"""Entrypoint for the generic MCP socket app."""

from __future__ import annotations

import asyncio

from .adapters import DockerSdkAdapter
from .server import DockerSocketServer
from .services import SocketOperationRegistry
from .settings import SOCKET_APP_SOCKET_PATH


def main() -> None:
    """Start the Unix-socket MCP helper app."""

    operation_registry = SocketOperationRegistry(backend=DockerSdkAdapter())
    server = DockerSocketServer(
        socket_path=SOCKET_APP_SOCKET_PATH,
        operation_registry=operation_registry,
    )
    try:
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
