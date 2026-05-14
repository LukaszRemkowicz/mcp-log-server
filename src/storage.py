"""Filesystem storage paths for collected log artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from conf import settings
from settings import REPOSITORY_ROOT, Settings


@dataclass(frozen=True, slots=True)
class LogFileStorage:
    """Resolve local filesystem paths for collected log artifacts."""

    root: Path | None = None
    config: Settings | None = None

    @property
    def location(self) -> Path:
        """Return the configured storage root."""

        return self.resolve_repo_path(self.root or self._settings.LOGS_DIR)

    @property
    def workflow_path(self) -> Path:
        """Return the root path for workflow log snapshots."""

        return self.location / "workflow"

    def workflow_latest_dir(self, project_name: str) -> Path:
        """Return the latest workflow snapshot directory for one project."""

        return self.workflow_path / project_name / "latest"

    def workflow_archive_dir(self, project_name: str) -> Path:
        """Return the workflow archive root directory for one project."""

        return self.workflow_path / project_name / "archive"

    def workflow_snapshot_paths(self, project_name: str) -> tuple[Path, Path]:
        """Return workflow latest and archive paths for one project."""

        return self.workflow_latest_dir(project_name), self.workflow_archive_dir(project_name)

    @property
    def session_path(self) -> Path:
        """Return the root path for session log snapshots."""

        return self.location / "sessions"

    def session_project_dir(self, session_id: str, project_name: str) -> Path:
        """Return one session project snapshot directory."""

        return self.session_path / session_id / project_name

    @staticmethod
    def ensure_dir(path: Path) -> Path:
        """Ensure one directory exists on disk and return it."""

        path.mkdir(parents=True, exist_ok=True)
        return path

    def path(self, name: str | Path) -> Path:
        """Return a safe absolute path under the storage root."""

        relative_path = Path(name)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("Storage path names must be relative and stay under LOGS_DIR.")
        return self.location / relative_path

    def relative_name(self, path: str | Path) -> str:
        """Return a storage-relative name for one path under the storage root."""

        resolved_root = self.location.resolve()
        resolved_path = Path(path).resolve()
        try:
            return resolved_path.relative_to(resolved_root).as_posix()
        except ValueError as error:
            raise ValueError("Storage paths must live under LOGS_DIR.") from error

    @property
    def _settings(self) -> Settings:
        """Return storage config, defaulting to process settings."""

        return self.config or settings

    @staticmethod
    def resolve_repo_path(path: Path) -> Path:
        """Resolve relative config paths against the repository root."""

        return path if path.is_absolute() else REPOSITORY_ROOT / path


storage = LogFileStorage()
