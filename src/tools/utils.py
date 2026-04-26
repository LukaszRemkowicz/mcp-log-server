"""Shared helpers for deterministic MCP tools."""

from __future__ import annotations

from fastmcp.server.auth import AccessToken

from manifests.loader import load_project_manifest
from manifests.models import SourceDefinition, SourceManifest
from settings import Settings


def load_authorized_project_manifest(
    settings: Settings,
    access_token: AccessToken,
    requested_project_name: str | None,
) -> tuple[SourceManifest, str, str]:
    """Resolve and authorize one project manifest for deterministic project tools."""

    authorized_project_name = str(access_token.claims.get("project_key") or "").strip()
    if not authorized_project_name:
        raise ValueError("Authenticated access token must include a project_key claim.")

    effective_project_name = requested_project_name or authorized_project_name
    if effective_project_name != authorized_project_name:
        raise ValueError(
            "Requested project key does not match the project_key authorized by the access token."
        )

    manifests_dir = settings.manifest_path.parent
    try:
        manifest = load_project_manifest(manifests_dir, effective_project_name)
    except FileNotFoundError as error:
        raise ValueError(
            f"Unknown project {effective_project_name!r}. No manifest file was "
            "found for that project."
        ) from error
    if manifest.project_key != effective_project_name:
        raise ValueError("Requested project key does not match the loaded manifest project_key.")

    return manifest, authorized_project_name, effective_project_name


def resolve_container_source_definition(
    manifest: SourceManifest,
    source_key: str,
) -> SourceDefinition:
    """Return one docker source definition enabled for container inspection."""

    definition = next(
        (source for source in manifest.sources if source.source_key == source_key),
        None,
    )
    if definition is None:
        raise ValueError("Requested source_key was not found in the configured manifest.")
    if definition.source_type != "docker":
        raise ValueError("Container file inspection is only available for docker sources.")
    if not definition.inspect_path_prefixes:
        raise ValueError("Container file inspection is not enabled for the requested source.")
    return definition
