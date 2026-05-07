"""Project manifest loading service for deterministic MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from conf import settings
from manifests.loader import load_project_manifest
from manifests.models import Manifest, SourceDefinition
from tools.models import ProjectManifestList, ProjectManifestSummary


@dataclass(frozen=True, slots=True)
class ProjectManifestContext:
    """Loaded manifest plus the project name used to resolve it."""

    manifest: Manifest
    project_name: str


@dataclass(frozen=True, slots=True)
class ManifestSources:
    """Selected manifest sources and request-resolution details.

    `sources` contains only source definitions that exist in the manifest.
    `missing_source_keys` echoes requested keys that were not found. `source_keys`
    is the resolved list that callers should use in response payloads.
    """

    sources: list[SourceDefinition]
    missing_source_keys: list[str]
    source_keys: list[str]


class ProjectManifestError(BaseModel):
    """Expected manifest lookup failure returned to deterministic tool layers."""

    message: str


class ProjectManifestService:
    """Load manifest-backed project context for deterministic MCP callers.

    This service owns manifest loading and manifest-derived source resolution.
    Project authorization belongs to the tool or middleware layer.
    """

    def _get_manifests_dir(self) -> Path:
        """Return the configured manifests directory for this service."""

        return settings.manifests_dir

    def get(
        self,
        project_name: str,
    ) -> ProjectManifestContext | None:
        """Load one project manifest when it exists and matches its file name.

        Returns `None` for missing manifests and for manifests whose
        `project_key` does not match the requested project name. The caller is
        responsible for turning `None` into a tool-specific error response.
        """

        try:
            manifest = load_project_manifest(self._get_manifests_dir(), project_name)
        except FileNotFoundError:
            return None

        if manifest.project_key != project_name:
            return None

        return ProjectManifestContext(
            manifest=manifest,
            project_name=project_name,
        )

    def get_or_error(
        self,
        project_name: str,
    ) -> ProjectManifestContext | ProjectManifestError:
        """Load one manifest or return a structured missing-project error."""

        manifest_context = self.get(project_name)
        if manifest_context is not None:
            return manifest_context
        return ProjectManifestError(
            message=(
                f"Unknown project {project_name!r}. No manifest file was found for that project."
            )
        )

    def _all_contexts(
        self,
    ) -> list[ProjectManifestContext]:
        """Return all loaded manifest contexts from the configured manifests directory."""

        manifests_dir = self._get_manifests_dir()
        resolved_project_names = sorted(path.stem for path in manifests_dir.glob("*.json"))
        manifest_contexts: list[ProjectManifestContext] = []
        for project_name in resolved_project_names:
            manifest_context = self.get(project_name)
            if manifest_context is not None:
                manifest_contexts.append(manifest_context)
        return manifest_contexts

    @staticmethod
    def _build_summary(manifest_context: ProjectManifestContext) -> ProjectManifestSummary:
        """Build one manifest-backed project summary from loaded context."""

        return ProjectManifestSummary(
            project_name=manifest_context.project_name,
            project_summary=manifest_context.manifest.project_summary,
            source_keys=[source.source_key for source in manifest_context.manifest.sources],
        )

    def all(
        self,
    ) -> ProjectManifestList:
        """Return lightweight summaries for every valid configured manifest."""

        manifest_contexts = self._all_contexts()
        data: list[ProjectManifestSummary] = [
            self._build_summary(manifest_context) for manifest_context in manifest_contexts
        ]
        return ProjectManifestList.model_validate(data)

    @staticmethod
    def get_manifest_source_keys(
        manifest: Manifest,
        source_keys: list[str] | None,
    ) -> ManifestSources:
        """Resolve requested source keys against one manifest.

        `None` and `["all"]` select every source. Any other list is resolved in
        manifest order while preserving missing requested keys separately, so
        collection can proceed with available sources and still warn about
        unknown ones.
        """

        if source_keys is None or source_keys == ["all"]:
            return ManifestSources(
                sources=list(manifest.sources),
                missing_source_keys=[],
                source_keys=[source.source_key for source in manifest.sources],
            )

        requested_lookup = set(source_keys)
        selected_sources = [
            source for source in manifest.sources if source.source_key in requested_lookup
        ]
        selected_source_keys = [source.source_key for source in selected_sources]
        missing_source_keys = [
            source_key for source_key in source_keys if source_key not in selected_source_keys
        ]
        return ManifestSources(
            sources=selected_sources,
            missing_source_keys=missing_source_keys,
            source_keys=selected_source_keys,
        )

    @staticmethod
    def get_container_source(
        manifest: Manifest,
        source_key: str,
    ) -> SourceDefinition:
        """Return one docker source that is enabled for container inspection.

        Raises:
            ValueError: When the source key is unknown, is not docker-backed, or
                does not declare approved inspection path prefixes.
        """

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

    def get_container_source_or_error(
        self,
        manifest: Manifest,
        source_key: str,
    ) -> SourceDefinition | ProjectManifestError:
        """Return one container-inspection source or a structured lookup error."""

        try:
            return self.get_container_source(manifest, source_key)
        except ValueError as error:
            return ProjectManifestError(message=str(error))
