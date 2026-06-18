"""Internal Docker-socket-owning app.

The package exposes the small public surface needed by tests and container
entrypoints. Implementation details remain in focused modules:

- `adapters` for Docker SDK access
- `services` for fixed operation validation and routing
- `server` for Unix-socket IO
"""

from .adapters import DockerSdkAdapter
from .dispatcher import dispatch_request
from .exceptions import DockerSocketAppException, ProtocolException
from .server import DockerSocketServer
from .services import DockerSocketService

__all__ = [
    "DockerSocketAppException",
    "DockerSdkAdapter",
    "DockerSocketServer",
    "DockerSocketService",
    "ProtocolException",
    "dispatch_request",
]
