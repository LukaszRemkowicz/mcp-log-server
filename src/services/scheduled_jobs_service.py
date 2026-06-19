"""Bounded scheduler provenance inspection for project-owned jobs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from conf import settings

SchedulerType = Literal["cron_d", "cron_daily", "cron_weekly", "crontab", "systemd", "unknown"]
SchedulerWarningCode = Literal[
    "scheduler_root_not_absolute",
    "scheduler_root_missing",
    "scheduler_root_not_directory",
    "scheduler_root_unreadable",
    "scheduler_file_unreadable",
    "scheduler_file_too_large",
    "scheduler_scan_truncated",
    "scheduler_pattern_ignored",
]

MAX_SCHEDULER_FILE_BYTES = 200_000
MAX_SCHEDULER_FILES = 400
MAX_SCHEDULER_MATCHES = 100
MAX_SCHEDULER_PATTERNS = 20
MAX_SCHEDULER_PATTERN_LENGTH = 120
_OUTPUT_REDIRECTION_PATTERN = re.compile(
    r"(?:^|\s)(?:\d?>{1,2}|&>|tee(?:\s+-a)?)\s+(?P<path>[^\s;&|]+)"
)


@dataclass(frozen=True, slots=True)
class ScheduledJobWarning:
    """Describe a skipped or degraded scheduler inspection path."""

    path: str | None
    warning_code: SchedulerWarningCode
    message: str


@dataclass(frozen=True, slots=True)
class ScheduledJobMatch:
    """One deterministic scheduler evidence match."""

    scheduler_type: SchedulerType
    path: str
    line_number: int | None
    schedule_context: str | None
    command_text: str
    output_paths: list[str]
    matched_patterns: list[str]
    visibility_warnings: list[str]


@dataclass(frozen=True, slots=True)
class ScheduledJobInspection:
    """Scheduler provenance inspection result for one authorized project."""

    project_name: str
    patterns: list[str]
    scheduler_roots: list[str]
    matches: list[ScheduledJobMatch]
    warnings: list[ScheduledJobWarning]
    truncated: bool


class ScheduledJobsService:
    """Inspect bounded cron/systemd scheduler files for project provenance."""

    def inspect_project_scheduled_jobs(
        self,
        project_name: str,
        patterns: list[str] | None = None,
        *,
        roots: list[Path] | None = None,
    ) -> ScheduledJobInspection:
        """Return scheduler evidence matching project-scoped literal patterns."""

        normalized_patterns, warnings = self._normalized_patterns(project_name, patterns)
        configured_roots = roots if roots is not None else list(settings.SCHEDULER_INSPECTION_ROOTS)
        scheduler_roots = [root.as_posix() for root in configured_roots]
        matches: list[ScheduledJobMatch] = []
        truncated = False
        inspected_files = 0

        for root in configured_roots:
            if not root.is_absolute():
                warnings.append(
                    ScheduledJobWarning(
                        path=root.as_posix(),
                        warning_code="scheduler_root_not_absolute",
                        message="Configured scheduler inspection roots must be absolute paths.",
                    )
                )
                continue
            if not root.exists():
                warnings.append(
                    ScheduledJobWarning(
                        path=root.as_posix(),
                        warning_code="scheduler_root_missing",
                        message="Configured scheduler inspection root was not found.",
                    )
                )
                continue
            if not root.is_dir():
                warnings.append(
                    ScheduledJobWarning(
                        path=root.as_posix(),
                        warning_code="scheduler_root_not_directory",
                        message="Configured scheduler inspection root is not a directory.",
                    )
                )
                continue

            try:
                entries = sorted(root.iterdir(), key=lambda item: item.name)
            except OSError:
                warnings.append(
                    ScheduledJobWarning(
                        path=root.as_posix(),
                        warning_code="scheduler_root_unreadable",
                        message="Configured scheduler inspection root could not be listed.",
                    )
                )
                continue

            for entry in entries:
                if inspected_files >= MAX_SCHEDULER_FILES:
                    truncated = True
                    warnings.append(
                        ScheduledJobWarning(
                            path=root.as_posix(),
                            warning_code="scheduler_scan_truncated",
                            message="Scheduler inspection stopped at the configured file limit.",
                        )
                    )
                    break
                if not entry.is_file():
                    continue
                inspected_files += 1
                file_matches, file_warnings = self._inspect_scheduler_file(
                    entry,
                    root,
                    normalized_patterns,
                )
                matches.extend(file_matches)
                warnings.extend(file_warnings)
                if len(matches) >= MAX_SCHEDULER_MATCHES:
                    truncated = True
                    matches = matches[:MAX_SCHEDULER_MATCHES]
                    warnings.append(
                        ScheduledJobWarning(
                            path=root.as_posix(),
                            warning_code="scheduler_scan_truncated",
                            message="Scheduler inspection stopped at the configured match limit.",
                        )
                    )
                    break
            if truncated:
                break

        return ScheduledJobInspection(
            project_name=project_name,
            patterns=normalized_patterns,
            scheduler_roots=scheduler_roots,
            matches=matches,
            warnings=warnings,
            truncated=truncated,
        )

    def _inspect_scheduler_file(
        self,
        path: Path,
        root: Path,
        patterns: list[str],
    ) -> tuple[list[ScheduledJobMatch], list[ScheduledJobWarning]]:
        """Return matches and warnings for one scheduler file."""

        warnings: list[ScheduledJobWarning] = []
        try:
            size = path.stat().st_size
        except OSError:
            return [], [
                ScheduledJobWarning(
                    path=path.as_posix(),
                    warning_code="scheduler_file_unreadable",
                    message="Scheduler file metadata could not be read.",
                )
            ]
        if size > MAX_SCHEDULER_FILE_BYTES:
            return [], [
                ScheduledJobWarning(
                    path=path.as_posix(),
                    warning_code="scheduler_file_too_large",
                    message="Scheduler file exceeded the bounded inspection size.",
                )
            ]

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return [], [
                ScheduledJobWarning(
                    path=path.as_posix(),
                    warning_code="scheduler_file_unreadable",
                    message="Scheduler file could not be read.",
                )
            ]

        scheduler_type = self._scheduler_type_for_path(path, root)
        if scheduler_type == "systemd":
            return self._systemd_matches(path, text, patterns), warnings
        return self._cron_matches(path, text, patterns, scheduler_type), warnings

    @staticmethod
    def _cron_matches(
        path: Path,
        text: str,
        patterns: list[str],
        scheduler_type: SchedulerType,
    ) -> list[ScheduledJobMatch]:
        """Return literal pattern matches from cron-style scheduler files."""

        matches: list[ScheduledJobMatch] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            matched_patterns = _matched_patterns(stripped, patterns)
            if not matched_patterns:
                continue
            schedule_context, command_text = _split_cron_line(stripped, scheduler_type)
            matches.append(
                ScheduledJobMatch(
                    scheduler_type=scheduler_type,
                    path=path.as_posix(),
                    line_number=line_number,
                    schedule_context=schedule_context,
                    command_text=command_text,
                    output_paths=_extract_output_paths(stripped),
                    matched_patterns=matched_patterns,
                    visibility_warnings=[],
                )
            )
        return matches

    @staticmethod
    def _systemd_matches(
        path: Path,
        text: str,
        patterns: list[str],
    ) -> list[ScheduledJobMatch]:
        """Return literal pattern matches from one systemd unit file."""

        if not _matched_patterns(path.name, patterns) and not _matched_patterns(text, patterns):
            return []

        matches: list[ScheduledJobMatch] = []
        current_section: str | None = None
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                current_section = stripped.strip("[]")
                continue
            matched_patterns = _matched_patterns(f"{path.name} {stripped}", patterns)
            if not matched_patterns:
                continue
            schedule_context = current_section
            if "=" in stripped:
                key, value = stripped.split("=", 1)
                schedule_context = f"{current_section}.{key}" if current_section else key
                command_text = value
            else:
                command_text = stripped
            matches.append(
                ScheduledJobMatch(
                    scheduler_type="systemd",
                    path=path.as_posix(),
                    line_number=line_number,
                    schedule_context=schedule_context,
                    command_text=command_text,
                    output_paths=_extract_output_paths(command_text),
                    matched_patterns=matched_patterns,
                    visibility_warnings=[],
                )
            )
        if matches:
            return matches

        matched_patterns = _matched_patterns(path.name, patterns)
        return [
            ScheduledJobMatch(
                scheduler_type="systemd",
                path=path.as_posix(),
                line_number=None,
                schedule_context="unit_name",
                command_text=path.name,
                output_paths=[],
                matched_patterns=matched_patterns,
                visibility_warnings=["Only the systemd unit name matched the requested patterns."],
            )
        ]

    @staticmethod
    def _scheduler_type_for_path(path: Path, root: Path) -> SchedulerType:
        """Classify one file based on the configured scheduler root shape."""

        root_text = root.as_posix()
        if root_text.endswith("/cron.d"):
            return "cron_d"
        if root_text.endswith("/cron.daily"):
            return "cron_daily"
        if root_text.endswith("/cron.weekly"):
            return "cron_weekly"
        if root_text.endswith("/spool/cron"):
            return "crontab"
        if root_text.endswith("/systemd/system") or path.suffix in {".service", ".timer"}:
            return "systemd"
        return "unknown"

    @staticmethod
    def _normalized_patterns(
        project_name: str,
        patterns: list[str] | None,
    ) -> tuple[list[str], list[ScheduledJobWarning]]:
        """Return safe literal substring patterns and ignored-pattern warnings."""

        raw_patterns = patterns if patterns is not None else [project_name]
        normalized_patterns: list[str] = []
        warnings: list[ScheduledJobWarning] = []
        for raw_pattern in raw_patterns:
            pattern = str(raw_pattern).strip().lower()
            if not pattern:
                warnings.append(
                    ScheduledJobWarning(
                        path=None,
                        warning_code="scheduler_pattern_ignored",
                        message="Empty scheduler inspection pattern was ignored.",
                    )
                )
                continue
            if len(pattern) > MAX_SCHEDULER_PATTERN_LENGTH:
                pattern = pattern[:MAX_SCHEDULER_PATTERN_LENGTH]
                warnings.append(
                    ScheduledJobWarning(
                        path=None,
                        warning_code="scheduler_pattern_ignored",
                        message="Scheduler inspection pattern was truncated to the safe limit.",
                    )
                )
            if pattern not in normalized_patterns:
                normalized_patterns.append(pattern)
            if len(normalized_patterns) >= MAX_SCHEDULER_PATTERNS:
                warnings.append(
                    ScheduledJobWarning(
                        path=None,
                        warning_code="scheduler_pattern_ignored",
                        message="Extra scheduler inspection patterns were ignored.",
                    )
                )
                break
        if not normalized_patterns:
            normalized_patterns.append(project_name.lower())
        return normalized_patterns, warnings


def _matched_patterns(text: str, patterns: list[str]) -> list[str]:
    """Return literal patterns found in text, case-insensitively."""

    lowered_text = text.lower()
    return [pattern for pattern in patterns if pattern in lowered_text]


def _extract_output_paths(command_text: str) -> list[str]:
    """Return simple shell redirection targets visible in a scheduler command."""

    output_paths: list[str] = []
    for match in _OUTPUT_REDIRECTION_PATTERN.finditer(command_text):
        output_path = match.group("path").strip("'\"")
        if output_path and output_path not in output_paths:
            output_paths.append(output_path)
    return output_paths


def _split_cron_line(
    line: str,
    scheduler_type: SchedulerType,
) -> tuple[str | None, str]:
    """Split one cron-like line into schedule context and command text."""

    if "=" in line and not line.startswith(("@", "*")):
        key, _value = line.split("=", 1)
        if key.strip().replace("_", "").isalnum():
            return "environment", line

    parts = line.split()
    if scheduler_type == "cron_d" and len(parts) >= 7:
        return " ".join(parts[:6]), " ".join(parts[6:])
    if scheduler_type in {"crontab", "unknown"} and len(parts) >= 6:
        return " ".join(parts[:5]), " ".join(parts[5:])
    if scheduler_type in {"cron_daily", "cron_weekly"}:
        return scheduler_type, line
    return None, line
