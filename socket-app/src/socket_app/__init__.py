"""Internal Docker-socket-owning app.

The package exposes the small public surface needed by tests and container
entrypoints. Implementation details remain in focused modules:

- `adapters` for Docker SDK access
- `services` for Docker helpers and fixed operation routing
- `server` for Unix-socket IO
"""

from .adapters import DockerSdkAdapter
from .dispatcher import dispatch_request
from .exceptions import DockerSocketAppException, ProtocolException
from .server import DockerSocketServer
from .services import SocketOperationRegistry

__all__ = [
    "DockerSocketAppException",
    "DockerSdkAdapter",
    "DockerSocketServer",
    "ProtocolException",
    "SocketOperationRegistry",
    "dispatch_request",
]
