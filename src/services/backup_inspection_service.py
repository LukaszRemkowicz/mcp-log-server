"""Bounded filesystem-metadata inspection for project backup files."""

from __future__ import annotations

import errno
import fcntl
import os
import stat
from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path
from typing import cast

from logging_config import get_logger
from manifests.models import (
    MAX_BACKUP_FILENAME_PATTERNS,
    MAX_BACKUP_LOCATIONS,
    ProjectBackupInspectionMetadata,
)
from services.schemas import (
    INTEGRITY_NOTE,
    BackupInspection,
    BackupInspectionStatus,
    BackupInspectionWarning,
)

MAX_BACKUP_DIRECTORY_ENTRIES = 1000
FUTURE_MTIME_TOLERANCE_SECONDS = 300
logger = get_logger("services.backup_inspection")


class BackupInspectionService:
    """Inspect backup directories without reading backup file contents.

    The service only trusts manifest locations that remain inside configured
    inspection roots, rejects symlink and path-swap escapes, scans direct
    regular files only, and returns bounded filesystem metadata for matching
    backup filenames.
    """

    def inspect(
        self,
        *,
        configuration: ProjectBackupInspectionMetadata,
        allowed_roots: list[Path],
        now: datetime | None = None,
        opened_directory_path_resolver: Callable[[int], Path] | None = None,
    ) -> BackupInspection:
        """Inspect configured backup locations and return newest matching file metadata.

        Directory handles may be opened to verify boundaries and scan entries,
        but matching backup files are never opened or read.
        """

        inspected_at = now or datetime.now(UTC)
        warnings: list[BackupInspectionWarning] = []
        candidates: list[tuple[os.stat_result, str]] = []
        inspected_entries = 0
        scan_truncated = False
        normalized_roots = self._normalized_allowed_roots(allowed_roots)
        resolver = opened_directory_path_resolver or self._resolve_open_directory_path
        patterns = configuration.filename_patterns[:MAX_BACKUP_FILENAME_PATTERNS]
        locations = configuration.locations[:MAX_BACKUP_LOCATIONS]
        configuration_truncated = (
            len(configuration.locations) > MAX_BACKUP_LOCATIONS
            or len(configuration.filename_patterns) > MAX_BACKUP_FILENAME_PATTERNS
        )
        if configuration_truncated:
            self._append_scan_truncated_warning(warnings)

        for configured_location in locations:
            inspected_entries, location_truncated = self._inspect_location(
                location=Path(configured_location),
                normalized_roots=normalized_roots,
                resolver=resolver,
                patterns=patterns,
                candidates=candidates,
                warnings=warnings,
                inspected_entries=inspected_entries,
            )
            scan_truncated = scan_truncated or location_truncated
            if scan_truncated:
                break

        if not candidates:
            return self._empty_inspection(
                warnings=warnings,
                configuration_truncated=configuration_truncated,
                scan_truncated=scan_truncated,
            )

        return self._candidate_inspection(
            candidates=candidates,
            inspected_at=inspected_at,
            max_age_seconds=configuration.max_age_seconds,
            configuration_truncated=configuration_truncated,
            scan_truncated=scan_truncated,
            warnings=warnings,
        )

    def _inspect_location(
        self,
        *,
        location: Path,
        normalized_roots: list[Path],
        resolver: Callable[[int], Path],
        patterns: list[str],
        candidates: list[tuple[os.stat_result, str]],
        warnings: list[BackupInspectionWarning],
        inspected_entries: int,
    ) -> tuple[int, bool]:
        """Validate one configured location before scanning its opened directory.

        The requested path must be inside an allowed root and must not be a
        symlink before the directory is opened. The opened directory is then
        delegated for race-resistant verification and scanning.
        """

        requested_path = location.absolute()
        if not self._path_is_under_roots(requested_path, normalized_roots):
            logger.warning(
                "backup inspection skipped configured location outside allowed roots",
                extra={
                    "event": "backup_inspection_location_not_allowed",
                    "warning_code": "backup_location_not_allowed",
                },
            )
            warnings.append(
                BackupInspectionWarning(
                    warning_code="backup_location_not_allowed",
                    message=("A manifest backup location is outside configured inspection roots."),
                )
            )
            return inspected_entries, False
        if location.is_symlink():
            logger.warning(
                "backup inspection skipped configured symlink location",
                extra={
                    "event": "backup_inspection_location_symlink",
                    "warning_code": "backup_location_symlink",
                },
            )
            warnings.append(
                BackupInspectionWarning(
                    warning_code="backup_location_symlink",
                    message="A manifest backup location is a symlink and was not inspected.",
                )
            )
            return inspected_entries, False
        try:
            directory_fd = os.open(location, self._directory_open_flags())
        except OSError as error:
            warning = self._open_error_warning(error)
            logger.warning(
                "backup inspection could not open configured location",
                extra={
                    "event": "backup_inspection_location_open_failed",
                    "warning_code": warning.warning_code,
                    "errno": error.errno,
                    "error_type": type(error).__name__,
                },
            )
            warnings.append(warning)
            return inspected_entries, False
        with ExitStack() as stack:
            stack.callback(os.close, directory_fd)
            return self._inspect_open_location(
                location=location,
                requested_path=requested_path,
                directory_fd=directory_fd,
                normalized_roots=normalized_roots,
                resolver=resolver,
                patterns=patterns,
                candidates=candidates,
                warnings=warnings,
                inspected_entries=inspected_entries,
            )

    def _inspect_open_location(
        self,
        *,
        location: Path,
        requested_path: Path,
        directory_fd: int,
        normalized_roots: list[Path],
        resolver: Callable[[int], Path],
        patterns: list[str],
        candidates: list[tuple[os.stat_result, str]],
        warnings: list[BackupInspectionWarning],
        inspected_entries: int,
    ) -> tuple[int, bool]:
        """Verify an opened location still matches the requested safe path.

        This catches resolver failures, opened paths outside the allowed roots,
        and path swaps between validation and scanning.
        """

        try:
            opened_path = resolver(directory_fd).resolve(strict=True)
        except OSError as error:
            logger.warning(
                "backup inspection could not verify opened location",
                extra={
                    "event": "backup_inspection_opened_location_verify_failed",
                    "warning_code": "backup_location_unreadable",
                    "errno": error.errno,
                    "error_type": type(error).__name__,
                },
            )
            warnings.append(
                BackupInspectionWarning(
                    warning_code="backup_location_unreadable",
                    message="An opened backup location could not be verified.",
                )
            )
            return inspected_entries, False
        if not self._path_is_under_roots(opened_path, normalized_roots):
            logger.warning(
                "backup inspection opened location escaped allowed roots",
                extra={
                    "event": "backup_inspection_opened_location_not_allowed",
                    "warning_code": "backup_location_not_allowed",
                },
            )
            warnings.append(
                BackupInspectionWarning(
                    warning_code="backup_location_not_allowed",
                    message=("An opened backup location is outside configured inspection roots."),
                )
            )
            return inspected_entries, False
        if requested_path != opened_path or not self._path_still_matches_fd(
            location,
            directory_fd,
        ):
            logger.warning(
                "backup inspection skipped changed location",
                extra={
                    "event": "backup_inspection_location_changed",
                    "warning_code": "backup_location_changed",
                },
            )
            warnings.append(
                BackupInspectionWarning(
                    warning_code="backup_location_changed",
                    message="A backup location changed during inspection and was skipped.",
                )
            )
            return inspected_entries, False
        return self._scan_open_directory(
            directory_fd=directory_fd,
            patterns=patterns,
            candidates=candidates,
            warnings=warnings,
            inspected_entries=inspected_entries,
        )

    @staticmethod
    def _empty_inspection(
        *,
        warnings: list[BackupInspectionWarning],
        configuration_truncated: bool,
        scan_truncated: bool,
    ) -> BackupInspection:
        """Build a missing or unavailable result when no candidates were found."""

        status: BackupInspectionStatus = "unavailable" if warnings else "missing"
        return BackupInspection(
            status=status,
            latest_filename=None,
            latest_age_seconds=None,
            latest_size_bytes=None,
            backup_count=0,
            scan_complete=not (configuration_truncated or scan_truncated),
            integrity_note=INTEGRITY_NOTE,
            warnings=warnings,
        )

    @staticmethod
    def _candidate_inspection(
        *,
        candidates: list[tuple[os.stat_result, str]],
        inspected_at: datetime,
        max_age_seconds: int,
        configuration_truncated: bool,
        scan_truncated: bool,
        warnings: list[BackupInspectionWarning],
    ) -> BackupInspection:
        """Build a result from candidates and classify freshness or truncation status."""

        latest_stat, latest_filename = max(candidates, key=lambda candidate: candidate[0].st_mtime)
        age_seconds = max(0, int(inspected_at.timestamp() - latest_stat.st_mtime))
        future_mtime = (
            latest_stat.st_mtime - inspected_at.timestamp() > FUTURE_MTIME_TOLERANCE_SECONDS
        )
        if future_mtime:
            logger.warning(
                "backup inspection latest backup mtime is in the future",
                extra={
                    "event": "backup_inspection_mtime_in_future",
                    "warning_code": "backup_mtime_in_future",
                    "future_seconds": int(latest_stat.st_mtime - inspected_at.timestamp()),
                },
            )
            warnings.append(
                BackupInspectionWarning(
                    warning_code="backup_mtime_in_future",
                    message="The latest backup modification time is unexpectedly in the future.",
                )
            )
        if configuration_truncated or scan_truncated or future_mtime:
            status: BackupInspectionStatus = "unavailable"
        elif age_seconds > max_age_seconds:
            status = "stale"
        else:
            status = "current"
        return BackupInspection(
            status=status,
            latest_filename=latest_filename,
            latest_age_seconds=age_seconds,
            latest_size_bytes=latest_stat.st_size,
            backup_count=len(candidates),
            scan_complete=not (configuration_truncated or scan_truncated),
            integrity_note=INTEGRITY_NOTE,
            warnings=warnings,
        )

    @staticmethod
    def _normalized_allowed_roots(roots: list[Path]) -> list[Path]:
        """Return existing absolute, non-symlink roots usable for boundary checks."""

        normalized_roots: list[Path] = []
        for root in roots:
            if not root.is_absolute() or root.is_symlink():
                continue
            try:
                normalized_root = root.resolve(strict=True)
            except OSError:
                continue
            if normalized_root.is_dir():
                normalized_roots.append(normalized_root)
        return normalized_roots

    @staticmethod
    def _directory_open_flags() -> int:
        """Return safe read-only directory-open flags for location inspection."""

        return (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )

    @staticmethod
    def _resolve_open_directory_path(directory_fd: int) -> Path:
        """Resolve one opened directory file descriptor to its filesystem path.

        Linux exposes file descriptors through procfs. Darwin exposes the path
        through F_GETPATH. Callers use the resolved path only for boundary and
        path-swap checks.
        """

        proc_path = Path("/proc/self/fd") / str(directory_fd)
        try:
            return Path(os.readlink(proc_path))
        except FileNotFoundError:
            get_path_command = cast("int | None", getattr(fcntl, "F_GETPATH", None))
            if get_path_command is None:
                raise OSError(
                    errno.ENOTSUP,
                    "Opened directory path resolution is not supported on this platform.",
                ) from None
            raw_path = fcntl.fcntl(directory_fd, get_path_command, b"\0" * 1024)
            path_bytes = raw_path.split(b"\0", 1)[0]
            if not path_bytes:
                raise OSError(errno.ENOENT, "Opened directory path was empty.")
            return Path(os.fsdecode(path_bytes))

    @staticmethod
    def _path_is_under_roots(path: Path, roots: list[Path]) -> bool:
        """Return whether an absolute path is inside one allowed inspection root."""

        return any(path == root or path.is_relative_to(root) for root in roots)

    @staticmethod
    def _path_still_matches_fd(path: Path, directory_fd: int) -> bool:
        """Return whether the configured path still points at the opened directory."""

        try:
            path_stat = os.stat(path, follow_symlinks=False)
            opened_stat = os.fstat(directory_fd)
        except OSError:
            return False
        return (
            stat.S_ISDIR(path_stat.st_mode)
            and path_stat.st_dev == opened_stat.st_dev
            and path_stat.st_ino == opened_stat.st_ino
        )

    @staticmethod
    def _open_error_warning(error: OSError) -> BackupInspectionWarning:
        """Convert one directory-open failure into a path-safe warning."""

        if isinstance(error, FileNotFoundError):
            return BackupInspectionWarning(
                warning_code="backup_location_missing",
                message="A manifest backup location does not exist.",
            )
        if isinstance(error, NotADirectoryError):
            return BackupInspectionWarning(
                warning_code="backup_location_not_directory",
                message="A manifest backup location is not a directory.",
            )
        if error.errno == errno.ELOOP:
            return BackupInspectionWarning(
                warning_code="backup_location_symlink",
                message="A manifest backup location is a symlink and was not inspected.",
            )
        return BackupInspectionWarning(
            warning_code="backup_location_unreadable",
            message="A manifest backup location could not be opened.",
        )

    def _scan_open_directory(
        self,
        *,
        directory_fd: int,
        patterns: list[str],
        candidates: list[tuple[os.stat_result, str]],
        warnings: list[BackupInspectionWarning],
        inspected_entries: int,
    ) -> tuple[int, bool]:
        """Scan direct directory entries and collect matching regular-file metadata."""

        try:
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    if inspected_entries >= MAX_BACKUP_DIRECTORY_ENTRIES:
                        self._append_scan_truncated_warning(warnings)
                        return inspected_entries, True
                    inspected_entries += 1
                    if not any(fnmatchcase(entry.name, pattern) for pattern in patterns):
                        continue
                    try:
                        file_stat = os.stat(
                            entry.name,
                            dir_fd=directory_fd,
                            follow_symlinks=False,
                        )
                    except OSError:
                        continue
                    if stat.S_ISREG(file_stat.st_mode):
                        candidates.append((file_stat, entry.name))
        except OSError as error:
            logger.warning(
                "backup inspection could not scan opened location",
                extra={
                    "event": "backup_inspection_opened_location_scan_failed",
                    "warning_code": "backup_location_unreadable",
                    "errno": error.errno,
                    "error_type": type(error).__name__,
                },
            )
            warnings.append(
                BackupInspectionWarning(
                    warning_code="backup_location_unreadable",
                    message="An opened backup location could not be scanned.",
                )
            )
        return inspected_entries, False

    @staticmethod
    def _append_scan_truncated_warning(warnings: list[BackupInspectionWarning]) -> None:
        """Append the scan or configuration truncation warning once."""

        if any(warning.warning_code == "backup_scan_truncated" for warning in warnings):
            return
        logger.warning(
            "backup inspection stopped at configured scan limit",
            extra={
                "event": "backup_inspection_scan_truncated",
                "warning_code": "backup_scan_truncated",
            },
        )
        warnings.append(
            BackupInspectionWarning(
                warning_code="backup_scan_truncated",
                message="Backup inspection stopped at a configured scan limit.",
            )
        )
