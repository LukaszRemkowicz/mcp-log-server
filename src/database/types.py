"""Database enum types shared by ORM models and services."""

from __future__ import annotations

from enum import StrEnum


class AgentCallEvent(StrEnum):
    """Known MCP audit event names."""

    MCP_CALL_TOOL = "mcp_call_tool"
    MCP_CALL_TOOL_EXCEPTION = "mcp_call_tool_exception"
    MCP_LIST_TOOLS = "mcp_list_tools"
    MCP_READ_RESOURCE = "mcp_read_resource"


class AgentSessionStatus(StrEnum):
    """Known interactive agent session lifecycle states."""

    ACTIVE = "active"
    CLOSED = "closed"


class LogSourceType(StrEnum):
    """Known manifest source types that can produce persisted log files."""

    DOCKER = "docker"
    FILE = "file"


class LogStream(StrEnum):
    """Known stream labels for collected sources."""

    STDOUT = "stdout"
    STDERR = "stderr"


class CollectLogsSourceStatus(StrEnum):
    """Known per-source collection statuses."""

    COLLECTED = "collected"
    UNAVAILABLE = "unavailable"
