"""Dependency injection helpers for FastMCP components."""

from __future__ import annotations

from utils.assets import WorkflowAssetLoader


def get_workflow_asset_loader() -> WorkflowAssetLoader:
    """Return a workflow-asset loader for dependency injection."""

    return WorkflowAssetLoader()
