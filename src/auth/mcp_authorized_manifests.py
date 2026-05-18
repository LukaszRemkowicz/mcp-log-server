"""Request-state model for caller-authorized project manifests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from auth.mcp_caller_context import AuthenticatedMcpCaller
from manifests.models import Manifest

AUTHORIZED_MANIFESTS_REQUEST_STATE_ATTR = "authorized_manifests"


@dataclass(frozen=True, slots=True)
class AuthorizedProjectManifests:
    """Project manifests the authenticated MCP caller may access."""

    caller: AuthenticatedMcpCaller
    manifests: Mapping[str, Manifest]


def freeze_authorized_manifests(
    manifests: dict[str, Manifest],
) -> Mapping[str, Manifest]:
    """Return a read-only mapping for request-scoped authorized manifests."""

    return MappingProxyType(dict(manifests))
