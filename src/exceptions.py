"""Project exceptions."""


class InvalidTimeFilterError(ValueError):
    """Raised when collect_logs since/until cannot be parsed for Docker logs."""


class MissingSessionIdError(ValueError):
    """Raised when session workspace setup runs without an MCP-provided session id."""


class DockerSocketGatewayError(Exception):
    """Expected Docker socket gateway failure."""

    def __init__(
        self,
        *,
        message: str,
        error_code: str = "socket_app_gateway_error",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
