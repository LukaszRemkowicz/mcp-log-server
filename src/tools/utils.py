"""Shared generic helpers for deterministic MCP tools."""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.models import LogSnapshotMetadata

RETENTION_DURATION_PATTERN = re.compile(
    r"^(?P<value>\d+)\s*(?P<unit>s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$",
    re.IGNORECASE,
)
SNAPSHOT_METADATA_FILE_NAME = "snapshot_metadata.json"


class SourceKeyArgumentError(ValueError):
    """Raised when one tool receives an invalid source_key/source_keys shape."""


def parse_snapshot_retention(value: str) -> timedelta:
    """Parse one snapshot retention setting like `30d`, `7days`, `1h`, or `10m`."""

    normalized_value = value.strip()
    duration_match = RETENTION_DURATION_PATTERN.fullmatch(normalized_value)
    if duration_match is None:
        raise ValueError(
            f"Invalid snapshot retention value {value!r}. Use values like 10m, 1h, 7d, or 30days."
        )

    duration_value = int(duration_match.group("value"))
    duration_unit = duration_match.group("unit").lower()
    if duration_unit in {"s", "sec", "secs", "second", "seconds"}:
        return timedelta(seconds=duration_value)
    if duration_unit in {"m", "min", "mins", "minute", "minutes"}:
        return timedelta(minutes=duration_value)
    if duration_unit in {"h", "hr", "hrs", "hour", "hours"}:
        return timedelta(hours=duration_value)
    return timedelta(days=duration_value)


def cleanup_old_snapshot_dirs(root_dir: Path, *, retention: timedelta) -> None:
    """Delete snapshot directories older than the configured retention window.

    This is quiet on missing roots because "nothing has been collected yet" is
    a normal state, not an agent-facing error.
    """

    if not root_dir.exists():
        return

    cutoff = datetime.now(UTC) - retention
    for entry in root_dir.iterdir():
        if not entry.is_dir():
            continue
        entry_modified_at = datetime.fromtimestamp(entry.stat().st_mtime, UTC)
        if entry_modified_at < cutoff:
            shutil.rmtree(entry)


def load_snapshot_metadata_from_json(metadata_json: str) -> LogSnapshotMetadata:
    """Load one current snapshot metadata file into the typed tool contract."""

    metadata = json.loads(metadata_json)
    metadata.pop("snapshot_id", None)
    return LogSnapshotMetadata.model_validate(metadata)


def resolve_source_keys_alias(
    source_keys: list[str] | None,
    source_key: str | None,
) -> list[str] | None:
    """Return canonical source_keys from plural input or the single-source alias."""

    if source_keys is not None and source_key is not None:
        raise SourceKeyArgumentError("Provide either source_key or source_keys, not both.")
    if source_keys is not None:
        return source_keys
    if source_key is None:
        return None
    stripped_source_key = source_key.strip()
    if not stripped_source_key:
        raise SourceKeyArgumentError("source_key must be a non-empty string.")
    return [stripped_source_key]
