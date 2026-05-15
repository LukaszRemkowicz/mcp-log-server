"""Application cache helpers."""

from __future__ import annotations

from typing import Any

from aiocache import Cache, cached

SIX_HOURS_SECONDS = 6 * 60 * 60


cached_for_6_hours = cached(ttl=SIX_HOURS_SECONDS, cache=Cache.MEMORY)


async def clear_cache(cached_callable: Any) -> None:
    """Clear one decorated callable cache."""

    await cached_callable.cache.clear()
