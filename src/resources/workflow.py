"""Workflow resources exposed through MCP."""

from fastmcp.resources.types import TextResource
from fastmcp.server.auth import require_scopes
from pydantic import AnyUrl, TypeAdapter

from auth.scopes import WORKFLOW_SKILLS_READ_SCOPE
from skills.workflow import WORKFLOW_SKILLS
from tools import mcp
from utils.assets import WorkflowAssetLoader

RESOURCE_URI_ADAPTER = TypeAdapter(AnyUrl)


def register_workflow_skill_resources() -> None:
    """Register the fixed workflow skill inventory as concrete MCP resources."""

    asset_loader = WorkflowAssetLoader()
    auth_check = require_scopes(WORKFLOW_SKILLS_READ_SCOPE)

    for definition in WORKFLOW_SKILLS:
        mcp.add_resource(
            TextResource(
                uri=RESOURCE_URI_ADAPTER.validate_python(definition.resource_uri),
                name=definition.skill_name,
                title=definition.skill_name,
                description=definition.description,
                mime_type="text/plain",
                auth=auth_check,
                text=asset_loader.load_text(definition.asset_path),
            )
        )


register_workflow_skill_resources()
