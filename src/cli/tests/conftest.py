"""Pytest fixtures for command tests colocated under src/cli."""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator

import pytest


@pytest.fixture
def database_test_case_sync() -> Generator[None]:
    """Satisfy the shared suite hook without DB setup for pure command tests."""

    yield


@pytest.fixture
async def database_test_case_async() -> AsyncIterator[None]:
    """Satisfy the shared suite hook without DB setup for async command tests."""

    yield
