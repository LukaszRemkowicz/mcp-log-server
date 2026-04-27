"""Dependency injection helpers for FastMCP components."""

from typing import TYPE_CHECKING

from conf import get_settings
from utils.assets import WorkflowAssetLoader

if TYPE_CHECKING:
    from settings import Settings


def get_settings_dependency() -> Settings:
    """Return the process settings for dependency injection."""

    return get_settings()


def get_workflow_asset_loader() -> WorkflowAssetLoader:
    """Return a workflow-asset loader for dependency injection."""

    return WorkflowAssetLoader()
