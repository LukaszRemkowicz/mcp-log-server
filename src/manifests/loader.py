"""Load and validate project source manifests."""

from __future__ import annotations

import json
from pathlib import Path

from manifests.models import SourceManifest


def load_manifest(path: str | Path) -> SourceManifest:
    """Load a project manifest from JSON and validate its structure."""

    manifest_path = Path(path)
    raw_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return SourceManifest.model_validate(raw_payload)


def load_project_manifest(manifests_dir: str | Path, project_name: str) -> SourceManifest:
    """Load one project manifest from the manifests directory by project name."""

    manifest_path = Path(manifests_dir) / f"{project_name}.json"
    return load_manifest(manifest_path)


def list_project_manifests(manifests_dir: str | Path) -> list[SourceManifest]:
    """Load all project manifests from one manifests directory."""

    manifests_path = Path(manifests_dir)
    return [load_manifest(path) for path in sorted(manifests_path.glob("*.json"))]
