"""Workflow resources exposed through MCP."""

from fastmcp.dependencies import Depends
from fastmcp.resources.base import ResourceResult
from fastmcp.resources.types import TextResource
from fastmcp.server.auth import require_scopes
from pydantic import AnyUrl, TypeAdapter

from app import mcp
from auth.scopes import WORKFLOW_SKILLS_READ_SCOPE
from dependencies import get_workflow_asset_loader
from skills.workflow import WORKFLOW_SKILLS, get_workflow_skill_definition
from utils.assets import WorkflowAssetLoader
from utils.mcp_errors import build_agent_resource_error_result

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


@mcp.resource(
    "skill://workflow/{skill_name}",
    name="workflow_skill_resource",
    description="Read one workflow skill as an MCP resource.",
    mime_type="text/plain",
    auth=require_scopes(WORKFLOW_SKILLS_READ_SCOPE),
)
def read_workflow_skill_resource(
    skill_name: str,
    asset_loader: WorkflowAssetLoader = Depends(get_workflow_asset_loader),
) -> str | ResourceResult:
    """Read one workflow skill and return structured guidance for invalid skill URIs."""

    try:
        definition = get_workflow_skill_definition(skill_name)
    except ValueError:
        return build_agent_resource_error_result(
            error_code="unknown_workflow_skill",
            message=f"Unknown workflow skill resource: {skill_name!r}.",
            retry_tips=[
                "Retry with one of the skill://workflow/... URIs returned by resources/list.",
                (
                    "Call analyze_daily_log_bundle to get the workflow skill inventory "
                    "before retrying."
                ),
            ],
            details={
                "requested_skill_name": skill_name,
                "available_skill_uris": [definition.resource_uri for definition in WORKFLOW_SKILLS],
            },
        )

    return asset_loader.load_text(definition.asset_path)
