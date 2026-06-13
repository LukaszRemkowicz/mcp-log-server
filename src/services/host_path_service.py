"""Manifest-bounded host filesystem inspection service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from manifests.models import SourceDefinition

MAX_PROJECT_FILE_BYTES = 200_000
MAX_PROJECT_DIRECTORY_ENTRIES = 200


class HostPathServiceError(BaseModel):
    """Expected host path inspection failure returned to the MCP tool layer."""

    message: str


@dataclass(frozen=True, slots=True)
class HostPathMetadata:
    """Metadata for one manifest-allowlisted host path."""

    path: str
    name: str
    exists: bool
    is_file: bool
    is_dir: bool
    is_symlink: bool
    size: int | None
    mode: int | None
    modified_at: str | None
    uid: int | None
    gid: int | None
    readable: bool
    symlink_target: str | None


class HostPathService:
    """Inspect host files and directories through manifest-bounded allowlists."""

    def stat_project_path(
        self,
        definition: SourceDefinition,
        path: str | None,
    ) -> HostPathMetadata | HostPathServiceError:
        """Return metadata for one approved host path, defaulting to the source target."""

        resolved_path = self._resolve_requested_path_or_error(definition, path, default="target")
        if isinstance(resolved_path, HostPathServiceError):
            return resolved_path
        allowed_error = self._validate_allowed_path(definition, resolved_path)
        if allowed_error is not None:
            return allowed_error
        return self._metadata_for_path(resolved_path)

    def list_project_directory(
        self,
        definition: SourceDefinition,
        path: str | None,
    ) -> tuple[list[HostPathMetadata], bool] | HostPathServiceError:
        """Return immediate metadata entries for one approved host directory."""

        resolved_path = self._resolve_requested_path_or_error(
            definition,
            path,
            default="parent",
        )
        if isinstance(resolved_path, HostPathServiceError):
            return resolved_path
        allowed_error = self._validate_allowed_path(definition, resolved_path)
        if allowed_error is not None:
            return allowed_error
        if not resolved_path.exists():
            return HostPathServiceError(message="Requested project path was not found.")
        if not resolved_path.is_dir():
            return HostPathServiceError(message="Requested project path is not a directory.")

        entries = sorted(resolved_path.iterdir(), key=lambda item: item.name)
        truncated = len(entries) > MAX_PROJECT_DIRECTORY_ENTRIES
        metadata_entries: list[HostPathMetadata] = []
        for entry in entries[:MAX_PROJECT_DIRECTORY_ENTRIES]:
            allowed_entry_error = self._validate_allowed_path(definition, entry)
            if allowed_entry_error is not None:
                continue
            metadata_entries.append(self._metadata_for_path(entry))
        return metadata_entries, truncated

    def read_project_file(
        self,
        definition: SourceDefinition,
        path: str | None,
        *,
        max_bytes: int = MAX_PROJECT_FILE_BYTES,
    ) -> tuple[str, bool] | HostPathServiceError:
        """Read a bounded UTF-8 text preview from one approved host file."""

        resolved_path = self._resolve_requested_path_or_error(definition, path, default="target")
        if isinstance(resolved_path, HostPathServiceError):
            return resolved_path
        allowed_error = self._validate_allowed_path(definition, resolved_path)
        if allowed_error is not None:
            return allowed_error
        if not resolved_path.exists():
            return HostPathServiceError(message="Requested project path was not found.")
        if not resolved_path.is_file():
            return HostPathServiceError(message="Requested project path is not a file.")
        safe_max_bytes = max(0, min(max_bytes, MAX_PROJECT_FILE_BYTES))
        with resolved_path.open("rb") as file:
            data = file.read(safe_max_bytes + 1)
        truncated = len(data) > safe_max_bytes
        return data[:safe_max_bytes].decode("utf-8", errors="replace"), truncated

    @staticmethod
    def _resolve_requested_path_or_error(
        definition: SourceDefinition,
        path: str | None,
        *,
        default: str,
    ) -> Path | HostPathServiceError:
        """Resolve one requested path without allowing traversal syntax."""

        if definition.source_type != "file":
            return HostPathServiceError(
                message="Project host path inspection is only available for file sources."
            )

        if path is None or not path.strip():
            target_path = Path(definition.target)
            return target_path.parent if default == "parent" else target_path
        if ".." in Path(path).parts:
            return HostPathServiceError(
                message="Project path inspection may not include parent directory traversal."
            )
        requested_path = Path(path)
        if not requested_path.is_absolute():
            return HostPathServiceError(
                message="Project path inspection path must be an absolute path."
            )
        return requested_path

    @staticmethod
    def _allowed_roots(definition: SourceDefinition) -> list[Path]:
        """Return manifest-derived host roots that callers may inspect."""

        target_path = Path(definition.target)
        roots = [target_path, target_path.parent]
        roots.extend(Path(prefix) for prefix in definition.inspect_path_prefixes)
        return roots

    def _validate_allowed_path(
        self,
        definition: SourceDefinition,
        path: Path,
    ) -> HostPathServiceError | None:
        """Return an error when path escapes the manifest-derived allowlist."""

        if not self._path_is_under_allowed_root(path, definition):
            return HostPathServiceError(
                message=(
                    "Requested project path is outside the manifest whitelist "
                    "for the selected source."
                )
            )

        if path.is_symlink():
            try:
                resolved_target = path.resolve(strict=True)
            except OSError:
                return HostPathServiceError(message="Requested project path was not found.")
            if not self._path_is_under_allowed_root(resolved_target, definition):
                return HostPathServiceError(
                    message=(
                        "Requested project path symlink resolves outside the manifest whitelist."
                    )
                )
        return None

    def _path_is_under_allowed_root(self, path: Path, definition: SourceDefinition) -> bool:
        """Return whether path is equal to or below any manifest-derived root."""

        candidate = path.absolute()
        for root in self._allowed_roots(definition):
            allowed_root = root.absolute()
            if candidate == allowed_root:
                return True
            try:
                candidate.relative_to(allowed_root)
                return True
            except ValueError:
                continue
        return False

    @staticmethod
    def _metadata_for_path(path: Path) -> HostPathMetadata:
        """Return stat-style metadata for one path without following symlink escapes."""

        try:
            stat_result = path.lstat()
        except FileNotFoundError:
            return HostPathMetadata(
                path=path.as_posix(),
                name=path.name,
                exists=False,
                is_file=False,
                is_dir=False,
                is_symlink=False,
                size=None,
                mode=None,
                modified_at=None,
                uid=None,
                gid=None,
                readable=False,
                symlink_target=None,
            )

        symlink_target = os.readlink(path) if path.is_symlink() else None
        modified_at = datetime.fromtimestamp(stat_result.st_mtime, UTC).isoformat()
        return HostPathMetadata(
            path=path.as_posix(),
            name=path.name or path.as_posix(),
            exists=True,
            is_file=path.is_file(),
            is_dir=path.is_dir(),
            is_symlink=path.is_symlink(),
            size=stat_result.st_size,
            mode=stat_result.st_mode,
            modified_at=modified_at,
            uid=stat_result.st_uid,
            gid=stat_result.st_gid,
            readable=os.access(path, os.R_OK),
            symlink_target=symlink_target,
        )
