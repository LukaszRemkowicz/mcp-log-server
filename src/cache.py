"""Application cache helpers.

These helpers use process-local in-memory cache storage. Invalidation only
works inside the Python process that owns the cache, so writes performed by a
shell command, migration command, or another container process do not clear the
already-running MCP app process cache. Do not use this module for operational
or security-sensitive DB state such as caller allowlists, project manifests, or
source routing unless the cache backend is replaced with a shared store.
"""

from __future__ import annotations

from typing import Any

from aiocache import Cache, cached

SIX_HOURS_SECONDS = 6 * 60 * 60


cached_for_6_hours = cached(ttl=SIX_HOURS_SECONDS, cache=Cache.MEMORY)
_namespace_caches: dict[str, list[Cache]] = {}


def cached_for_6_hours_in_namespace(namespace: str) -> Any:
    """Cache one callable for six hours and register it under a clearable namespace."""

    def decorate(func: Any) -> Any:
        decorated = cached(ttl=SIX_HOURS_SECONDS, cache=Cache.MEMORY, namespace=namespace)(func)
        _namespace_caches.setdefault(namespace, []).append(decorated.cache)
        return decorated

    return decorate


async def clear_cache(cached_callable: Any) -> None:
    """Clear one decorated callable cache."""

    await cached_callable.cache.clear()


async def clear_cache_namespace(namespace: str) -> None:
    """Clear all cached callables registered under one namespace."""

    for cache in _namespace_caches.get(namespace, []):
        await cache.clear(namespace=namespace)
