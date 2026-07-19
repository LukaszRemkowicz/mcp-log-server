from __future__ import annotations

from inspect import unwrap
from pathlib import Path

import pytest

from manifests.models import ProjectBackupInspectionMetadata
from tools import backups


@pytest.mark.anyio
async def test_backup_tool_offloads_filesystem_scan_to_worker_thread(monkeypatch) -> None:
    configuration = ProjectBackupInspectionMetadata(
        locations=["/host/var/backups/project/prod"],
        filename_patterns=["project_*.dump"],
        max_age_seconds=86400,
    )
    sentinel = object()
    captured: dict[str, object] = {}

    async def fake_to_thread(function, *args, **kwargs):
        captured["function"] = function
        captured["args"] = args
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr("decorators.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr(
        backups.settings,
        "BACKUP_INSPECTION_ROOTS",
        [Path("/host/var/backups")],
    )

    result = await backups._inspect_configured_backups(configuration)

    assert result is sentinel
    assert captured == {
        "function": unwrap(backups._inspect_configured_backups),
        "args": (configuration,),
        "kwargs": {},
    }
