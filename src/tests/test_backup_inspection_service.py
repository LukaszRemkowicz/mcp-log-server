from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from manifests.models import (
    MAX_BACKUP_FILENAME_PATTERNS,
    MAX_BACKUP_LOCATIONS,
    ProjectBackupInspectionMetadata,
)
from services.backup_inspection_service import BackupInspectionService

FUTURE_MTIME_TOLERANCE_SECONDS = 300
MAX_BACKUP_DIRECTORY_ENTRIES = 1000


def _configuration(directory: Path) -> ProjectBackupInspectionMetadata:
    return ProjectBackupInspectionMetadata(
        locations=[directory.as_posix()],
        filename_patterns=["landingpage_*.dump"],
        max_age_seconds=3600,
    )


def test_opened_directory_resolver_returns_the_real_directory_path(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups" / "landingpage"
    backup_dir.mkdir(parents=True)
    directory_fd = os.open(
        backup_dir,
        BackupInspectionService._directory_open_flags(),
    )
    try:
        resolved = BackupInspectionService._resolve_open_directory_path(directory_fd)
    finally:
        os.close(directory_fd)

    assert resolved.resolve(strict=True) == backup_dir.resolve(strict=True)


def test_backup_inspection_returns_latest_matching_regular_file_metadata(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups" / "landingpage"
    backup_dir.mkdir(parents=True)
    older = backup_dir / "landingpage_older.dump"
    latest = backup_dir / "landingpage_latest.dump"
    ignored = backup_dir / "other-project.dump"
    older.write_bytes(b"old")
    latest.write_bytes(b"latest backup")
    ignored.write_bytes(b"ignore")
    os.utime(older, (1_720_000_000, 1_720_000_000))
    os.utime(latest, (1_720_003_000, 1_720_003_000))

    result = BackupInspectionService().inspect(
        configuration=_configuration(backup_dir),
        allowed_roots=[tmp_path / "backups"],
        now=datetime.fromtimestamp(1_720_003_600, tz=UTC),
        opened_directory_path_resolver=lambda _fd: backup_dir,
    )

    assert result.status == "current"
    assert result.latest_filename == "landingpage_latest.dump"
    assert result.latest_age_seconds == 600
    assert result.latest_size_bytes == len(b"latest backup")
    assert result.backup_count == 2
    assert result.scan_complete is True
    assert result.integrity_note == (
        "Integrity was not independently verified; this tool inspects filesystem metadata only."
    )


def test_backup_inspection_excludes_symlinks_directories_and_nested_files(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups" / "landingpage"
    backup_dir.mkdir(parents=True)
    nested_dir = backup_dir / "landingpage_nested.dump"
    nested_dir.mkdir()
    (nested_dir / "landingpage_hidden.dump").write_bytes(b"hidden")
    outside = tmp_path / "landingpage_outside.dump"
    outside.write_bytes(b"outside")
    (backup_dir / "landingpage_link.dump").symlink_to(outside)

    result = BackupInspectionService().inspect(
        configuration=_configuration(backup_dir),
        allowed_roots=[tmp_path / "backups"],
        now=datetime(2026, 7, 19, tzinfo=UTC),
        opened_directory_path_resolver=lambda _fd: backup_dir,
    )

    assert result.status == "missing"
    assert result.latest_filename is None
    assert result.latest_age_seconds is None
    assert result.latest_size_bytes is None
    assert result.backup_count == 0


def test_backup_inspection_rejects_manifest_location_outside_settings_roots(
    tmp_path: Path,
) -> None:
    backup_dir = tmp_path / "unapproved"
    backup_dir.mkdir()
    (backup_dir / "landingpage_latest.dump").write_bytes(b"do not inspect")

    result = BackupInspectionService().inspect(
        configuration=_configuration(backup_dir),
        allowed_roots=[tmp_path / "approved"],
        now=datetime(2026, 7, 19, tzinfo=UTC),
        opened_directory_path_resolver=lambda _fd: backup_dir,
    )

    assert result.status == "unavailable"
    assert result.backup_count == 0
    assert result.latest_filename is None
    assert [warning.warning_code for warning in result.warnings] == ["backup_location_not_allowed"]
    assert backup_dir.as_posix() not in result.warnings[0].message


def test_backup_inspection_reports_final_configured_symlink(tmp_path: Path) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("O_NOFOLLOW is required to reject final symlink paths at open time.")
    backup_root = tmp_path / "backups"
    real_location = tmp_path / "real-backups"
    backup_root.mkdir()
    real_location.mkdir()
    symlink_location = backup_root / "landingpage"
    symlink_location.symlink_to(real_location, target_is_directory=True)

    result = BackupInspectionService().inspect(
        configuration=_configuration(symlink_location),
        allowed_roots=[backup_root],
    )

    assert result.status == "unavailable"
    assert result.backup_count == 0
    assert [warning.warning_code for warning in result.warnings] == ["backup_location_symlink"]


def test_backup_inspection_marks_old_latest_backup_stale(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups" / "landingpage"
    backup_dir.mkdir(parents=True)
    backup = backup_dir / "landingpage_latest.dump"
    backup.write_bytes(b"backup")
    os.utime(backup, (1_720_000_000, 1_720_000_000))

    result = BackupInspectionService().inspect(
        configuration=_configuration(backup_dir),
        allowed_roots=[tmp_path / "backups"],
        now=datetime.fromtimestamp(1_720_007_201, tz=UTC),
        opened_directory_path_resolver=lambda _fd: backup_dir,
    )

    assert result.status == "stale"
    assert result.latest_age_seconds == 7201


def test_backup_inspection_rejects_intermediate_symlink_outside_allowed_root(
    tmp_path: Path,
) -> None:
    allowed_root = tmp_path / "allowed"
    outside_root = tmp_path / "outside"
    outside_backup = outside_root / "project"
    allowed_root.mkdir()
    outside_backup.mkdir(parents=True)
    (outside_backup / "landingpage_latest.dump").write_bytes(b"outside")
    (allowed_root / "alias").symlink_to(outside_root, target_is_directory=True)
    configured_location = allowed_root / "alias" / "project"

    result = BackupInspectionService().inspect(
        configuration=_configuration(configured_location),
        allowed_roots=[allowed_root],
        opened_directory_path_resolver=lambda _fd: outside_backup,
    )

    assert result.status == "unavailable"
    assert result.backup_count == 0
    assert [warning.warning_code for warning in result.warnings] == ["backup_location_not_allowed"]


def test_backup_inspection_rejects_path_swapped_after_directory_open(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    configured_location = allowed_root / "project"
    moved_location = allowed_root / "opened-project"
    outside_location = tmp_path / "outside"
    configured_location.mkdir(parents=True)
    outside_location.mkdir()
    (configured_location / "landingpage_original.dump").write_bytes(b"original")
    (outside_location / "landingpage_outside.dump").write_bytes(b"outside")

    def swap_requested_path(_fd: int) -> Path:
        configured_location.rename(moved_location)
        configured_location.symlink_to(outside_location, target_is_directory=True)
        return moved_location

    result = BackupInspectionService().inspect(
        configuration=_configuration(configured_location),
        allowed_roots=[allowed_root],
        opened_directory_path_resolver=swap_requested_path,
    )

    assert result.status == "unavailable"
    assert result.backup_count == 0
    assert [warning.warning_code for warning in result.warnings] == ["backup_location_changed"]


def test_backup_inspection_stops_at_directory_entry_limit(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups" / "landingpage"
    backup_dir.mkdir(parents=True)
    for index in range(MAX_BACKUP_DIRECTORY_ENTRIES + 1):
        (backup_dir / f"landingpage_{index:04d}.dump").write_bytes(b"backup")

    result = BackupInspectionService().inspect(
        configuration=_configuration(backup_dir),
        allowed_roots=[tmp_path / "backups"],
        opened_directory_path_resolver=lambda _fd: backup_dir,
    )

    assert result.backup_count == MAX_BACKUP_DIRECTORY_ENTRIES
    assert result.scan_complete is False
    assert result.status == "unavailable"
    assert "backup_scan_truncated" in {warning.warning_code for warning in result.warnings}


def test_backup_inspection_rejects_latest_mtime_far_in_future(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups" / "landingpage"
    backup_dir.mkdir(parents=True)
    backup = backup_dir / "landingpage_future.dump"
    backup.write_bytes(b"backup")
    inspected_at = datetime(2026, 7, 19, tzinfo=UTC)
    future_timestamp = inspected_at.timestamp() + FUTURE_MTIME_TOLERANCE_SECONDS + 1
    os.utime(backup, (future_timestamp, future_timestamp))

    result = BackupInspectionService().inspect(
        configuration=_configuration(backup_dir),
        allowed_roots=[tmp_path / "backups"],
        now=inspected_at,
        opened_directory_path_resolver=lambda _fd: backup_dir,
    )

    assert result.status == "unavailable"
    assert result.latest_age_seconds == 0
    assert "backup_mtime_in_future" in {warning.warning_code for warning in result.warnings}


def test_backup_inspection_reports_missing_configured_directory(tmp_path: Path) -> None:
    allowed_root = tmp_path / "backups"
    allowed_root.mkdir()
    missing_directory = allowed_root / "missing"

    result = BackupInspectionService().inspect(
        configuration=_configuration(missing_directory),
        allowed_roots=[allowed_root],
    )

    assert result.status == "unavailable"
    assert result.backup_count == 0
    assert result.scan_complete is True
    assert [warning.warning_code for warning in result.warnings] == ["backup_location_missing"]


def test_backup_inspection_reports_configured_file_as_not_directory(tmp_path: Path) -> None:
    allowed_root = tmp_path / "backups"
    allowed_root.mkdir()
    configured_file = allowed_root / "backup.dump"
    configured_file.write_bytes(b"backup")

    result = BackupInspectionService().inspect(
        configuration=_configuration(configured_file),
        allowed_roots=[allowed_root],
    )

    assert result.status == "unavailable"
    assert result.backup_count == 0
    assert [warning.warning_code for warning in result.warnings] == [
        "backup_location_not_directory"
    ]


def test_backup_inspection_reports_resolver_oserror_as_unreadable(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups" / "landingpage"
    backup_dir.mkdir(parents=True)

    def unreadable_resolver(_directory_fd: int) -> Path:
        raise OSError("resolver failed")

    result = BackupInspectionService().inspect(
        configuration=_configuration(backup_dir),
        allowed_roots=[tmp_path / "backups"],
        opened_directory_path_resolver=unreadable_resolver,
    )

    assert result.status == "unavailable"
    assert result.backup_count == 0
    assert [warning.warning_code for warning in result.warnings] == ["backup_location_unreadable"]


def test_backup_inspection_counts_locations_and_chooses_newest_backup(tmp_path: Path) -> None:
    backup_root = tmp_path / "backups"
    first_directory = backup_root / "first"
    second_directory = backup_root / "second"
    first_directory.mkdir(parents=True)
    second_directory.mkdir()
    first_backup = first_directory / "landingpage_first.dump"
    second_backup = second_directory / "landingpage_second.dump"
    latest_backup = second_directory / "landingpage_latest.dump"
    first_backup.write_bytes(b"first")
    second_backup.write_bytes(b"second")
    latest_backup.write_bytes(b"latest")
    os.utime(first_backup, (1_720_000_000, 1_720_000_000))
    os.utime(second_backup, (1_720_001_000, 1_720_001_000))
    os.utime(latest_backup, (1_720_002_000, 1_720_002_000))
    configuration = ProjectBackupInspectionMetadata(
        locations=[first_directory.as_posix(), second_directory.as_posix()],
        filename_patterns=["landingpage_*.dump"],
        max_age_seconds=3600,
    )

    result = BackupInspectionService().inspect(
        configuration=configuration,
        allowed_roots=[backup_root],
        now=datetime.fromtimestamp(1_720_002_600, tz=UTC),
    )

    assert result.status == "current"
    assert result.latest_filename == "landingpage_latest.dump"
    assert result.latest_age_seconds == 600
    assert result.latest_size_bytes == len(b"latest")
    assert result.backup_count == 3
    assert result.scan_complete is True


def test_backup_inspection_truncates_excess_configured_locations(tmp_path: Path) -> None:
    backup_root = tmp_path / "backups"
    configured_directories = [
        backup_root / f"location-{index}" for index in range(MAX_BACKUP_LOCATIONS + 1)
    ]
    for directory in configured_directories:
        directory.mkdir(parents=True)
    (configured_directories[-1] / "landingpage_ignored.dump").write_bytes(b"ignored")
    configuration = ProjectBackupInspectionMetadata.model_construct(
        locations=[directory.as_posix() for directory in configured_directories],
        filename_patterns=["landingpage_*.dump"],
        max_age_seconds=3600,
    )

    result = BackupInspectionService().inspect(
        configuration=configuration,
        allowed_roots=[backup_root],
    )

    assert result.status == "unavailable"
    assert result.backup_count == 0
    assert result.scan_complete is False
    assert [warning.warning_code for warning in result.warnings] == ["backup_scan_truncated"]


def test_backup_inspection_truncates_excess_filename_patterns(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups" / "landingpage"
    backup_dir.mkdir(parents=True)
    (backup_dir / "landingpage_ignored.dump").write_bytes(b"ignored")
    configuration = ProjectBackupInspectionMetadata.model_construct(
        locations=[backup_dir.as_posix()],
        filename_patterns=[
            *(f"other-project-{index}.dump" for index in range(MAX_BACKUP_FILENAME_PATTERNS)),
            "landingpage_*.dump",
        ],
        max_age_seconds=3600,
    )

    result = BackupInspectionService().inspect(
        configuration=configuration,
        allowed_roots=[tmp_path / "backups"],
        opened_directory_path_resolver=lambda _fd: backup_dir,
    )

    assert result.status == "unavailable"
    assert result.backup_count == 0
    assert result.scan_complete is False
    assert [warning.warning_code for warning in result.warnings] == ["backup_scan_truncated"]


def test_backup_inspection_reports_scandir_failure_as_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_dir = tmp_path / "backups" / "landingpage"
    backup_dir.mkdir(parents=True)

    def fail_scandir(_directory_fd: int) -> None:
        raise OSError("scan failed")

    monkeypatch.setattr(os, "scandir", fail_scandir)

    result = BackupInspectionService().inspect(
        configuration=_configuration(backup_dir),
        allowed_roots=[tmp_path / "backups"],
        opened_directory_path_resolver=lambda _fd: backup_dir,
    )

    assert result.status == "unavailable"
    assert result.backup_count == 0
    assert result.scan_complete is True
    assert [warning.warning_code for warning in result.warnings] == ["backup_location_unreadable"]
