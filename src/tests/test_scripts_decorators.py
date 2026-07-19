"""Tests for project script helpers."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from decorators import async_, db, run_in_thread


def test_async_runs_coroutine_and_preserves_signature() -> None:
    async def command(project_name: str, all_projects: bool = False) -> str:
        return f"{project_name}:{all_projects}"

    wrapped = async_(command)

    assert wrapped("landingpage", all_projects=True) == "landingpage:True"
    assert inspect.signature(wrapped) == inspect.signature(command)


def test_run_in_thread_offloads_sync_function_and_preserves_signature(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    async def fake_to_thread(function, *args, **kwargs):
        calls.append(("to_thread", function))
        return function(*args, **kwargs)

    monkeypatch.setattr("decorators.asyncio.to_thread", fake_to_thread)

    def command(project_name: str, all_projects: bool = False) -> str:
        calls.append((project_name, all_projects))
        return f"{project_name}:{all_projects}"

    wrapped = run_in_thread(command)

    assert async_(wrapped)("landingpage", all_projects=True) == "landingpage:True"
    assert inspect.signature(wrapped) == inspect.signature(command)
    assert calls == [("to_thread", command), ("landingpage", True)]


def test_db_wraps_async_command_with_database_lifespan(monkeypatch) -> None:
    calls: list[str] = []

    @asynccontextmanager
    async def fake_database_lifespan(_app: object = None) -> AsyncIterator[None]:
        calls.append("enter")
        try:
            yield
        finally:
            calls.append("exit")

    monkeypatch.setattr("decorators.database_lifespan", fake_database_lifespan)

    @async_
    @db
    async def command(project_name: str) -> str:
        calls.append(project_name)
        return project_name.upper()

    assert command("landingpage") == "LANDINGPAGE"
    assert calls == ["enter", "landingpage", "exit"]
