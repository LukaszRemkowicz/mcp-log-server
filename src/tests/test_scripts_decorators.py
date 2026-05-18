"""Tests for project script helpers."""

from __future__ import annotations

import inspect

from decorators import async_


def test_async_runs_coroutine_and_preserves_signature() -> None:
    async def command(project_name: str, all_projects: bool = False) -> str:
        return f"{project_name}:{all_projects}"

    wrapped = async_(command)

    assert wrapped("landingpage", all_projects=True) == "landingpage:True"
    assert inspect.signature(wrapped) == inspect.signature(command)
