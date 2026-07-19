from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BackupInspectionStatus = Literal["current", "stale", "missing", "unavailable"]
BackupWarningCode = Literal[
    "backup_location_not_allowed",
    "backup_location_missing",
    "backup_location_not_directory",
    "backup_location_symlink",
    "backup_location_unreadable",
    "backup_location_changed",
    "backup_scan_truncated",
    "backup_mtime_in_future",
]
INTEGRITY_NOTE = (
    "Integrity was not independently verified; this tool inspects filesystem metadata only."
)


@dataclass(frozen=True, slots=True)
class BackupInspectionWarning:
    """Describe one skipped or unavailable configured backup location."""

    warning_code: BackupWarningCode
    message: str


@dataclass(frozen=True, slots=True)
class BackupInspection:
    """Return bounded backup metadata without exposing file paths or contents."""

    status: BackupInspectionStatus
    latest_filename: str | None
    latest_age_seconds: int | None
    latest_size_bytes: int | None
    backup_count: int
    scan_complete: bool
    integrity_note: str
    warnings: list[BackupInspectionWarning]
