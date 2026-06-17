"""Entrypoint for the Docker socket app."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .adapters import DockerSdkAdapter
from .server import DockerSocketServer
from .services import DockerSocketService

DEFAULT_SOCKET_PATH = "/run/docker-socket-app/docker.sock"


def main() -> None:
    """Start the Unix-socket Docker app."""

    socket_path = Path(os.environ.get("DOCKER_SOCKET_APP_SOCKET_PATH", DEFAULT_SOCKET_PATH))
    service = DockerSocketService(backend=DockerSdkAdapter())
    server = DockerSocketServer(socket_path=socket_path, service=service)
    try:
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
