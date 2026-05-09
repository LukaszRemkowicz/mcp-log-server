"""Custom database fields."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from tortoise import fields
from tortoise.exceptions import ConfigurationError
from tortoise.models import Model
from tortoise.validators import MaxLengthValidator


@dataclass(frozen=True, slots=True)
class FileStorage:
    """Small storage adapter used by FileReference path, URL, size, and open helpers."""

    location: str | Path | None = None
    base_url: str | None = None

    def path(self, name: str) -> str:
        """Return the filesystem path for a stored file name."""

        file_path = Path(name)
        if file_path.is_absolute() or self.location is None:
            return file_path.as_posix()
        return (Path(self.location) / file_path).as_posix()

    def url(self, name: str) -> str:
        """Return the public URL for a stored file name."""

        if self.base_url is None:
            return name
        return f"{self.base_url.rstrip('/')}/{quote(name.lstrip('/'))}"

    def size(self, name: str) -> int:
        """Return the file size in bytes."""

        return Path(self.path(name)).stat().st_size

    def open(self, name: str, mode: str = "rb"):
        """Open the stored file from the resolved filesystem path."""

        return Path(self.path(name)).open(mode)


@dataclass(frozen=True, slots=True)
class FileReference:
    """Django FieldFile-style value object for one stored file reference."""

    name: str
    storage: FileStorage | None = None
    size_bytes: int | None = None

    def __bool__(self) -> bool:
        return bool(self.name)

    def __str__(self) -> str:
        return self.name

    @property
    def path(self) -> str:
        """Return the filesystem path for this file reference."""

        if self.storage is None:
            return Path(self.name).as_posix()
        return self.storage.path(self.name)

    @property
    def url(self) -> str:
        """Return the URL for this file reference."""

        if self.storage is None:
            return self.name
        return self.storage.url(self.name)

    @property
    def size(self) -> int:
        """Return the file size in bytes."""

        if self.size_bytes is not None:
            return self.size_bytes
        if self.storage is not None:
            return self.storage.size(self.name)
        return Path(self.path).stat().st_size

    def open(self, mode: str = "rb"):
        """Open the referenced file from the resolved filesystem path."""

        if self.storage is not None:
            return self.storage.open(self.name, mode)
        return Path(self.path).open(mode)


class FileField(fields.Field[FileReference]):
    """Django-style file reference field stored as a database path string.

    The field stores only the file path/reference in the database, matching the
    important database behavior of Django's ``FileField``. Model values are
    returned as ``FileReference`` objects with Django-like ``name``, ``path``,
    ``url``, ``size``, and ``open()`` helpers.
    """

    field_type = FileReference

    def __init__(
        self,
        *,
        upload_to: str | Path | None = None,
        storage: FileStorage | None = None,
        max_length: int = 100,
        **kwargs: Any,
    ) -> None:
        if int(max_length) < 1:
            raise ConfigurationError("'max_length' must be >= 1")
        self.upload_to = None if upload_to is None else Path(upload_to).as_posix()
        self.storage = storage
        self.max_length = int(max_length)
        super().__init__(**kwargs)
        self.validators.append(MaxLengthValidator(self.max_length))

    @property
    def constraints(self) -> dict[str, int]:
        return {"max_length": self.max_length}

    @property
    def SQL_TYPE(self) -> str:  # type: ignore[override]
        return f"VARCHAR({self.max_length})"

    def _build_reference(self, value: str | Path | FileReference) -> FileReference:
        """Return a file reference for raw path-like input."""

        if isinstance(value, FileReference):
            storage = value.storage or self.storage
            if value.size_bytes is not None:
                return FileReference(
                    name=value.name,
                    storage=storage,
                    size_bytes=value.size_bytes,
                )
            file_reference = FileReference(name=value.name, storage=storage)
            resolved_path = Path(file_reference.path)
            if resolved_path.exists():
                return FileReference(
                    name=value.name,
                    storage=storage,
                    size_bytes=resolved_path.stat().st_size,
                )
            return file_reference
        name = Path(value).as_posix()
        file_reference = FileReference(
            name=name,
            storage=self.storage,
        )
        resolved_path = Path(file_reference.path)
        size_bytes = resolved_path.stat().st_size if resolved_path.exists() else None
        return FileReference(
            name=name,
            storage=self.storage,
            size_bytes=size_bytes,
        )

    def to_db_value(
        self,
        value: str | Path | FileReference | None,
        instance: type[Model] | Model,
    ) -> str | None:
        """Convert file reference input into the stored path string."""

        if value is None:
            return None
        file_reference = self._build_reference(value)
        self.validate(file_reference.name)
        return file_reference.name

    def to_python_value(
        self,
        value: str | Path | FileReference | None,
    ) -> FileReference | None:
        """Return the stored file reference as a Django-like value object."""

        if value is None:
            return None
        return self._build_reference(value)
