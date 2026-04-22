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
