"""Workflow skill inventory and loading helpers for the daily log agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class WorkflowSkillMetadata(TypedDict):
    """Structured workflow-skill metadata returned to agents."""

    skill_name: str
    description: str
    mandatory: bool
    resource_uri: str


@dataclass(frozen=True, slots=True)
class WorkflowSkillDefinition:
    """Describe one workflow skill exposed through the log-analysis workflow."""

    skill_name: str
    asset_path: str
    description: str
    mandatory: bool = False

    @property
    def resource_uri(self) -> str:
        """Return the MCP resource URI for this skill."""

        return f"skill://workflow/{self.skill_name}"


WORKFLOW_SKILLS: tuple[WorkflowSkillDefinition, ...] = (
    WorkflowSkillDefinition(
        skill_name="normal_patterns",
        asset_path="skills/normal_patterns.md",
        description="Known healthy log patterns that should not be treated as incidents.",
        mandatory=True,
    ),
    WorkflowSkillDefinition(
        skill_name="application_monitoring",
        asset_path="skills/application_monitoring.md",
        description="Checklist of application-level failures and log signals to watch.",
        mandatory=True,
    ),
    WorkflowSkillDefinition(
        skill_name="bot_detection",
        asset_path="skills/bot_detection.md",
        description="Guidance for recognizing bot traffic and suspicious probing patterns.",
    ),
    WorkflowSkillDefinition(
        skill_name="owasp_security",
        asset_path="skills/owasp_security.md",
        description="Security interpretation guidance aligned with OWASP-style incident framing.",
    ),
    WorkflowSkillDefinition(
        skill_name="severity_guide",
        asset_path="skills/severity_guide.md",
        description="Severity classification rules for final monitoring reports.",
        mandatory=True,
    ),
    WorkflowSkillDefinition(
        skill_name="recommendations_guide",
        asset_path="skills/recommendations_guide.md",
        description="Rules for producing concrete, project-relevant recommendations.",
        mandatory=True,
    ),
)


def list_workflow_skill_definitions() -> list[WorkflowSkillMetadata]:
    """Return the public workflow skill inventory for MCP callers."""

    return [
        {
            "skill_name": definition.skill_name,
            "description": definition.description,
            "mandatory": definition.mandatory,
            "resource_uri": definition.resource_uri,
        }
        for definition in WORKFLOW_SKILLS
    ]


def list_mandatory_workflow_skill_definitions() -> list[WorkflowSkillMetadata]:
    """Return the always-included workflow skill baseline."""

    return [
        {
            "skill_name": definition.skill_name,
            "description": definition.description,
            "mandatory": definition.mandatory,
            "resource_uri": definition.resource_uri,
        }
        for definition in WORKFLOW_SKILLS
        if definition.mandatory
    ]


def list_optional_workflow_skill_definitions() -> list[WorkflowSkillMetadata]:
    """Return workflow skills that can be requested on demand."""

    return [
        {
            "skill_name": definition.skill_name,
            "description": definition.description,
            "mandatory": definition.mandatory,
            "resource_uri": definition.resource_uri,
        }
        for definition in WORKFLOW_SKILLS
        if not definition.mandatory
    ]


def get_workflow_skill_definition(skill_name: str) -> WorkflowSkillDefinition:
    """Return one workflow skill definition by name."""

    for definition in WORKFLOW_SKILLS:
        if definition.skill_name == skill_name:
            return definition
    allowed = ", ".join(definition.skill_name for definition in WORKFLOW_SKILLS)
    raise ValueError(f"Unknown workflow skill {skill_name!r}. Allowed: {allowed}")
