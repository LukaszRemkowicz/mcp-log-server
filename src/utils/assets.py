"""Helpers for loading MCP-owned workflow assets from the repository."""

from __future__ import annotations

import json
from pathlib import Path

from utils.types import JSONObject, JSONValue


class WorkflowAssetLoader:
    """Load copied workflow assets that now live in this repository."""

    def __init__(self, base_dir: Path | None = None) -> None:
        resolved_base_dir = base_dir or Path(__file__).resolve().parents[1] / "agent_assets"
        self.base_dir = resolved_base_dir

    def get_path(self, relative_path: str) -> Path:
        """Resolve a workflow asset path relative to the copied asset bundle."""

        asset_path = self.base_dir / relative_path
        if not asset_path.exists():
            raise FileNotFoundError(f"Workflow asset not found: {asset_path}")
        return asset_path

    def load_text(self, relative_path: str) -> str:
        """Load a UTF-8 text asset."""

        return self.get_path(relative_path).read_text(encoding="utf-8")

    def load_json(self, relative_path: str) -> JSONObject:
        """Load a JSON asset that must be an object."""

        asset_path = self.get_path(relative_path)
        with asset_path.open(encoding="utf-8") as handle:
            payload: JSONValue = json.load(handle)

        if not isinstance(payload, dict):
            raise ValueError(f"Workflow asset JSON must be an object: {asset_path}")

        return payload
