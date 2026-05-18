"""Project exceptions."""


class InvalidTimeFilterError(ValueError):
    """Raised when collect_logs since/until cannot be parsed for Docker logs."""


class MissingSessionIdError(ValueError):
    """Raised when session workspace setup runs without an MCP-provided session id."""
