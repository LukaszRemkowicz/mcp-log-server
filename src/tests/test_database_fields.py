"""Tests for custom database field behavior."""

from __future__ import annotations

from pathlib import Path

import pytest
from tortoise.exceptions import ConfigurationError, ValidationError
from tortoise.models import Model

from database.fields import FileField, FileReference, FileStorage
from database.schemas import CollectLogsSourceOut


def test_file_storage_resolves_path_url_size_and_open(tmp_path: Path) -> None:
    storage = FileStorage(location=tmp_path, base_url="/logs")
    log_file = tmp_path / "workflow" / "landing page.log"
    log_file.parent.mkdir()
    log_file.write_text("line 1\nline 2\n", encoding="utf-8")

    reference = FileReference(name="workflow/landing page.log", storage=storage)

    assert reference.name == "workflow/landing page.log"
    assert str(reference) == "workflow/landing page.log"
    assert reference.path == (tmp_path / "workflow" / "landing page.log").as_posix()
    assert reference.url == "/logs/workflow/landing%20page.log"
    assert reference.size == len(b"line 1\nline 2\n")
    with reference.open("r") as handle:
        assert handle.read() == "line 1\nline 2\n"


def test_file_field_stores_only_file_name_and_returns_reference(tmp_path: Path) -> None:
    storage = FileStorage(location=tmp_path)
    log_file = tmp_path / "sessions" / "backend.log"
    log_file.parent.mkdir()
    log_file.write_bytes(b"log payload")
    field = FileField(storage=storage, max_length=255)

    reference = field.to_python_value("sessions/backend.log")

    assert reference == FileReference(
        name="sessions/backend.log",
        storage=storage,
        size_bytes=len(b"log payload"),
    )
    assert field.to_db_value(reference, Model) == "sessions/backend.log"


def test_file_field_accepts_path_values_without_storage(tmp_path: Path) -> None:
    log_file = tmp_path / "backend.log"
    log_file.write_text("payload", encoding="utf-8")
    field = FileField(max_length=255)

    reference = field.to_python_value(log_file)

    assert reference is not None
    assert reference.name == log_file.as_posix()
    assert reference.path == log_file.as_posix()
    assert reference.url == log_file.as_posix()
    assert reference.size == len(b"payload")


def test_file_field_returns_none_for_empty_database_values() -> None:
    field = FileField(max_length=255)

    assert field.to_python_value(None) is None
    assert field.to_db_value(None, Model) is None


def test_file_field_rejects_invalid_max_length() -> None:
    with pytest.raises(ConfigurationError, match="max_length"):
        FileField(max_length=0)


def test_file_field_validates_max_length_before_storing() -> None:
    field = FileField(max_length=3)

    with pytest.raises(ValidationError, match="Length"):
        field.to_db_value("too-long.log", Model)


def test_collect_logs_source_out_exposes_response_file_fields(tmp_path: Path) -> None:
    storage = FileStorage(location=tmp_path)
    log_file = tmp_path / "workflow" / "landingpage" / "latest" / "backend.log"
    log_file.parent.mkdir(parents=True)
    log_file.write_bytes(b"log payload")

    source = CollectLogsSourceOut(
        id=1,
        source_key="backend",
        source_type="docker",
        target="backend-container",
        description="Backend logs.",
        stream="stdout",
        status="collected",
        file=FileReference(
            name="workflow/landingpage/latest/backend.log",
            storage=storage,
        ),
        line_count=1,
        error=None,
        retry_tips=[],
    )

    assert source.output_file == "workflow/landingpage/latest/backend.log"
    assert source.byte_count == len(b"log payload")
