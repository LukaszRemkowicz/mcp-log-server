"""Fixed operation registry for the Docker socket app."""

from __future__ import annotations

from typing import Any

from .exceptions import ProtocolException
from .schemas import DockerBackend


class DockerSocketService:
    """Validate and dispatch fixed Docker operations.

    This class is the socket app's service layer. It sits between decoded
    JSON requests and the Docker backend:

    - validates that each request uses a known operation name
    - validates primitive parameter types before Docker is touched
    - calls one explicit backend method per operation
    - rejects shell-like or generic Docker requests by construction

    The class intentionally does not know about any consuming application,
    projects, users, manifests, JWTs, or audit records. A caller that needs
    higher-level authorization must do that before sending a request here. This
    app only provides a small local capability: fixed Docker reads over a Unix
    socket.

    Supported operation names:

    - `container_logs`
    - `container_health`
    - `container_detail`
    - `container_path_stat`
    - `container_file_read`
    - `container_directory_list`
    - `vps_containers_inventory`
    - `vps_volumes_inventory`
    - `traefik_router_tls_inventory`
    """

    def __init__(self, backend: DockerBackend) -> None:
        """Create a Docker socket service backed by one Docker implementation."""

        self.backend = backend

    def dispatch(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        """Validate and run one supported operation.

        Args:
            operation: Fixed operation name from the supported operation list.
            params: JSON object containing operation-specific parameters.

        Returns:
            A JSON-serializable result dictionary from the backend.

        Raises:
            ProtocolException: The operation name is unknown or the parameters do
                not match the fixed operation contract.
        """

        if operation == "container_logs":
            return self.backend.container_logs(
                container_name=self._required_string(params, "container_name"),
                since=self._optional_string(params, "since"),
                until=self._optional_string(params, "until"),
                tail=self._optional_int(params, "tail"),
            )
        if operation == "container_health":
            return self.backend.container_health(
                container_name=self._required_string(params, "container_name")
            )
        if operation == "container_detail":
            return self.backend.container_detail(
                container_name=self._required_string(params, "container_name")
            )
        if operation == "container_path_stat":
            return self.backend.container_path_stat(
                container_name=self._required_string(params, "container_name"),
                path=self._required_string(params, "path"),
            )
        if operation == "container_file_read":
            return self.backend.container_file_read(
                container_name=self._required_string(params, "container_name"),
                path=self._required_string(params, "path"),
                max_bytes=self._optional_int(params, "max_bytes"),
            )
        if operation == "container_directory_list":
            return self.backend.container_directory_list(
                container_name=self._required_string(params, "container_name"),
                path=self._required_string(params, "path"),
                max_entries=self._optional_int(params, "max_entries"),
            )
        if operation == "vps_containers_inventory":
            self._reject_params(params)
            return self.backend.vps_containers_inventory()
        if operation == "vps_volumes_inventory":
            return self.backend.vps_volumes_inventory(
                dangling_only=self._optional_bool(params, "dangling_only"),
                anonymous_only=self._optional_bool(params, "anonymous_only"),
                name_prefix=self._optional_string(params, "name_prefix"),
            )
        if operation == "traefik_router_tls_inventory":
            self._reject_params(params)
            return self.backend.traefik_router_tls_inventory()

        raise ProtocolException(f"Unsupported docker socket operation: {operation}")

    @staticmethod
    def _required_string(params: dict[str, Any], key: str) -> str:
        """Return a required non-empty string parameter or raise `ProtocolException`."""

        value = params.get(key)
        if not isinstance(value, str) or not value:
            raise ProtocolException(f"Parameter '{key}' must be a non-empty string.")
        return value

    @staticmethod
    def _optional_string(params: dict[str, Any], key: str) -> str | None:
        """Return an optional string parameter or raise `ProtocolException`."""

        value = params.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ProtocolException(f"Parameter '{key}' must be a string.")
        return value

    @staticmethod
    def _optional_int(params: dict[str, Any], key: str) -> int | None:
        """Return an optional integer parameter or raise `ProtocolException`."""

        value = params.get(key)
        if value is None:
            return None
        if not isinstance(value, int):
            raise ProtocolException(f"Parameter '{key}' must be an integer.")
        return value

    @staticmethod
    def _optional_bool(params: dict[str, Any], key: str) -> bool:
        """Return an optional boolean parameter with `False` as the default."""

        value = params.get(key)
        if value is None:
            return False
        if not isinstance(value, bool):
            raise ProtocolException(f"Parameter '{key}' must be a boolean.")
        return value

    @staticmethod
    def _reject_params(params: dict[str, Any]) -> None:
        """Reject parameters for operations whose contract takes no arguments."""

        if params:
            raise ProtocolException("This operation does not accept parameters.")
