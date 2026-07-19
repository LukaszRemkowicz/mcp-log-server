"""Services for the generic socket app."""

from __future__ import annotations

import json
from typing import Any, Literal

from docker.errors import APIError, DockerException
from requests import exceptions as requests_exceptions

from . import settings
from .exceptions import DockerBackendError, ProtocolException
from .schemas import DockerBackend


class DockerService:
    """Run bounded Docker SDK operations shared by socket services."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def get_container(self, container_name: str) -> Any:
        """Return one Docker container or raise a stable backend error."""

        try:
            return self.client.containers.get(container_name)
        except APIError as error:
            output = str(error).strip() or "Unknown docker error."
            if "No such container" in output:
                raise DockerBackendError(
                    f"Configured container {container_name!r} is not available "
                    "in the current runtime."
                ) from error
            raise DockerBackendError(output) from error
        except requests_exceptions.Timeout as error:
            raise DockerBackendError(
                f"Timed out inspecting container {container_name!r}."
            ) from error
        except DockerException as error:
            raise DockerBackendError(str(error).strip() or "Docker operation failed.") from error

    def run_text(self, *, container_name: str, command: list[str]) -> str:
        """Run one fixed command and return UTF-8 text output."""

        container = self.get_container(container_name)
        result = container.exec_run(command, stdout=True, stderr=True)
        exit_code = 0 if result.exit_code is None else int(result.exit_code)
        output = result.output.decode("utf-8", errors="replace")
        if exit_code != 0:
            normalized_output = output.strip()
            if "No such file or directory" in normalized_output or not normalized_output:
                raise DockerBackendError("Requested container path was not found.")
            raise DockerBackendError(normalized_output)
        return output

    def run_json(
        self,
        *,
        container_name: str,
        command: list[str],
        cwd: str,
        label: str,
    ) -> dict[str, Any]:
        """Run one fixed command and parse object JSON from stdout."""

        container = self.get_container(container_name)
        try:
            result = container.exec_run(
                command,
                demux=True,
                stderr=True,
                stdout=True,
                tty=False,
                workdir=cwd,
            )
        except requests_exceptions.Timeout as error:
            raise DockerBackendError(f"Timed out waiting for {label}.") from error
        except DockerException as error:
            raise DockerBackendError(f"{label} command is not available.") from error

        exit_code = getattr(result, "exit_code", None)
        output = getattr(result, "output", None)
        if exit_code is None or output is None:
            raise DockerBackendError(f"{label} command returned an invalid response.")

        stdout_text = self._stdout_text(output)
        if exit_code != 0:
            raise DockerBackendError(f"{label} command failed.")

        try:
            parsed = json.loads(stdout_text)
        except json.JSONDecodeError as error:
            raise DockerBackendError(f"{label} returned invalid JSON.") from error

        if not isinstance(parsed, dict):
            raise DockerBackendError(f"{label} returned a non-object JSON value.")
        return parsed

    @staticmethod
    def _stdout_text(output: Any) -> str:
        stdout_bytes: bytes | None
        if isinstance(output, tuple):
            stdout_bytes = output[0]
        elif isinstance(output, bytes):
            stdout_bytes = output
        else:
            stdout_bytes = None

        if stdout_bytes is None:
            return ""
        return stdout_bytes.decode("utf-8", errors="replace")


class CrowdSecService:
    """Run fixed read-only CrowdSec diagnostics."""

    def __init__(self, docker_service: DockerService) -> None:
        self.docker_service = docker_service

    def inspect_activity(self, *, container_name: str) -> dict[str, Any]:
        """Return fixed CrowdSec sections from one container."""

        commands = {
            "decisions": ["cscli", "decisions", "list"],
            "appsec_metrics": ["cscli", "metrics", "show", "appsec"],
            "bouncers": ["cscli", "bouncers", "list"],
            "alerts": ["cscli", "alerts", "list"],
            "collections": ["cscli", "collections", "list"],
        }
        results = {
            name: self._run_crowdsec_read(container_name=container_name, command=command)
            for name, command in commands.items()
        }
        return {"container_name": container_name, "sections": results}

    def _run_crowdsec_read(self, *, container_name: str, command: list[str]) -> dict[str, Any]:
        container = self.docker_service.get_container(container_name)
        result = container.exec_run(command, stdout=True, stderr=True)
        exit_code = 0 if result.exit_code is None else int(result.exit_code)
        output = result.output.decode("utf-8", errors="replace")
        encoded = output.encode("utf-8")
        truncated = len(encoded) > settings.MAX_CROWDSEC_OUTPUT_CHARS
        if truncated:
            output = encoded[: settings.MAX_CROWDSEC_OUTPUT_CHARS].decode("utf-8", errors="ignore")
        return {
            "command": command,
            "exit_code": exit_code,
            "ok": exit_code == 0,
            "output": output.strip(),
            "truncated": truncated,
        }


class BackendCommandRunService:
    """Run fixed backend command helpers."""

    def __init__(self, docker_service: DockerService) -> None:
        self.docker_service = docker_service

    def list_landingpage_commands(
        self, *, container_name: str, base_command: list[str], cwd: str
    ) -> dict[str, Any]:
        """Return landingpage command metadata from the configured command runner."""

        return self.docker_service.run_json(
            command=[*base_command, "mcp_list_commands", "--json"],
            container_name=container_name,
            cwd=cwd,
            label="Landingpage Django command discovery",
        )

    def inspect_landingpage_media_inventory(
        self, *, container_name: str, base_command: list[str], cwd: str
    ) -> dict[str, Any]:
        """Return landingpage media inventory from the configured command runner."""

        return self.docker_service.run_json(
            command=[*base_command, "media_inventory", "--json"],
            container_name=container_name,
            cwd=cwd,
            label="Landingpage media inventory",
        )


class SocketOperationRegistry:
    """Validate and dispatch fixed Docker operations.

    This class is the socket app's operation registry. It sits between decoded
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

    - `service_health`
    - `container_logs`
    - `container_logs_page`
    - `container_health`
    - `container_detail`
    - `container_path_stat`
    - `container_file_read`
    - `container_directory_list`
    - `vps_containers_inventory`
    - `vps_volumes_inventory`
    - `traefik_router_tls_inventory`
    - `crowdsec_activity`
    - `landingpage_django_list_commands`
    - `landingpage_django_media_inventory`
    """

    def __init__(self, backend: DockerBackend) -> None:
        """Create an operation registry backed by one Docker implementation."""

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

        if operation == "service_health":
            self._reject_params(params)
            return self.backend.service_health()
        if operation == "container_logs":
            return self.backend.container_logs(
                container_name=self._required_string(params, "container_name"),
                since=self._optional_string(params, "since"),
                until=self._optional_string(params, "until"),
                tail=self._optional_int(params, "tail"),
            )
        if operation == "container_logs_page":
            transfer_id = self._optional_string(params, "transfer_id")
            if transfer_id == "":
                raise ProtocolException("Parameter 'transfer_id' must be a non-empty string.")
            if transfer_id is not None:
                unexpected = set(params) - {"transfer_id", "offset", "max_bytes"}
                if unexpected:
                    raise ProtocolException(
                        "A continued log transfer accepts only transfer_id, offset, and max_bytes."
                    )
                return self.backend.container_logs_page(
                    transfer_id=transfer_id,
                    offset=self._optional_int(params, "offset") or 0,
                    max_bytes=self._optional_int(params, "max_bytes"),
                )
            return self.backend.container_logs_page(
                container_name=self._required_string(params, "container_name"),
                stream=self._optional_log_stream(params, "stream"),
                since=self._optional_string(params, "since"),
                until=self._optional_string(params, "until"),
                tail=self._optional_int(params, "tail"),
                offset=self._optional_int(params, "offset") or 0,
                max_bytes=self._optional_int(params, "max_bytes"),
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
        if operation == "crowdsec_activity":
            return self.backend.crowdsec_activity(
                container_name=self._required_string(params, "container_name")
            )
        if operation == "landingpage_django_list_commands":
            return self.backend.landingpage_django_list_commands(
                container_name=self._required_string(params, "container_name"),
                base_command=self._required_string_list(params, "base_command"),
                cwd=self._required_string(params, "cwd"),
            )
        if operation == "landingpage_django_media_inventory":
            return self.backend.landingpage_django_media_inventory(
                container_name=self._required_string(params, "container_name"),
                base_command=self._required_string_list(params, "base_command"),
                cwd=self._required_string(params, "cwd"),
            )

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
    def _optional_log_stream(
        params: dict[str, Any], key: str
    ) -> Literal["stdout", "stderr"] | None:
        """Return an optional supported Docker log stream label."""

        value = params.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or value not in {"stdout", "stderr"}:
            raise ProtocolException(f"Parameter '{key}' must be 'stdout' or 'stderr'.")
        return value

    @staticmethod
    def _required_string_list(params: dict[str, Any], key: str) -> list[str]:
        """Return a required non-empty string list or raise `ProtocolException`."""

        value = params.get(key)
        if not isinstance(value, list) or not value:
            raise ProtocolException(f"Parameter '{key}' must be a non-empty string list.")
        if any(not isinstance(item, str) or not item for item in value):
            raise ProtocolException(f"Parameter '{key}' must contain non-empty strings.")
        return value

    @staticmethod
    def _optional_int(params: dict[str, Any], key: str) -> int | None:
        """Return an optional integer parameter or raise `ProtocolException`."""

        value = params.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
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
