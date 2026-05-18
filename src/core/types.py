"""Core app-wide enum types."""

from __future__ import annotations

from enum import StrEnum


class LogWorkspace(StrEnum):
    """Known collection/audit workspaces."""

    WORKFLOW = "workflow"
    SESSION = "session"
