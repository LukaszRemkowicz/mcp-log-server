from __future__ import annotations

import pytest

from exceptions import DockerSocketGatewayError
from manifests.models import Manifest
from services.landingpage_django import CommandRunTarget, LandingpageDjangoCommands
from services.project_manifest import ProjectManifestContext
from tools.landingpage_django import (
    _landingpage_django_command_run_from_manifest,
    list_landingpage_django_commands,
)


@pytest.mark.asyncio
async def test_list_landingpage_django_commands_returns_connector_commands(mocker) -> None:
    mocker.patch(
        "decorators._get_allowed_projects",
        return_value=frozenset({"landingpage"}),
    )
    list_commands = mocker.patch(
        "tools.landingpage_django.landingpage_django_service.list_commands",
        return_value=LandingpageDjangoCommands(
            report={
                "commands": [
                    {
                        "name": "media_inventory",
                        "description": "Inspect DB image references and media files on disk.",
                        "read_only": True,
                        "params": {},
                    }
                ]
            }
        ),
    )
    mocker.patch(
        "tools.landingpage_django._resolve_landingpage_django_command_run",
        return_value=CommandRunTarget(
            container_name="portfolio-dev-be-1",
            base_command=("uv", "run", "python", "manage.py"),
            cwd="/app",
        ),
    )

    result = await list_landingpage_django_commands(project_name="landingpage")

    assert list_commands.call_count == 1
    list_commands.assert_called_once_with(
        command_run=CommandRunTarget(
            container_name="portfolio-dev-be-1",
            base_command=("uv", "run", "python", "manage.py"),
            cwd="/app",
        )
    )
    assert result.structured_content is not None
    assert result.structured_content["action"] == "list_landingpage_django_commands"
    assert result.structured_content["requested_project_name"] == "landingpage"
    assert result.structured_content["project_name"] == "landingpage"
    assert result.structured_content["connector_status"] == "ok"
    assert result.structured_content["report"]["commands"][0]["name"] == "media_inventory"


@pytest.mark.asyncio
async def test_list_landingpage_django_commands_maps_connector_error(mocker) -> None:
    mocker.patch(
        "decorators._get_allowed_projects",
        return_value=frozenset({"landingpage"}),
    )
    mocker.patch(
        "tools.landingpage_django.landingpage_django_service.list_commands",
        side_effect=DockerSocketGatewayError(
            message="Socket app is not available.",
            error_code="socket_app_unavailable",
        ),
    )
    mocker.patch(
        "tools.landingpage_django._resolve_landingpage_django_command_run",
        return_value=CommandRunTarget(
            container_name="portfolio-dev-be-1",
            base_command=("uv", "run", "python", "manage.py"),
            cwd="/app",
        ),
    )

    result = await list_landingpage_django_commands(project_name="landingpage")

    assert result.structured_content is not None
    assert result.structured_content["status"] == "error"
    assert result.structured_content["error_code"] == "socket_app_unavailable"
    assert result.structured_content["project_name"] == "landingpage"


@pytest.mark.asyncio
async def test_list_landingpage_django_commands_rejects_unauthorized_project(mocker) -> None:
    mocker.patch(
        "decorators._get_allowed_projects",
        return_value=frozenset({"landingpage"}),
    )

    result = await list_landingpage_django_commands(project_name="other")

    assert result.structured_content is not None
    assert result.structured_content["status"] == "error"
    assert result.structured_content["error_code"] == "project_access_mismatch"


def test_landingpage_django_command_run_resolves_from_manifest_source() -> None:
    manifest = Manifest.model_validate(
        {
            "project_key": "landingpage",
            "project_summary": "Landingpage test manifest.",
            "sources": [
                {
                    "source_key": "backend",
                    "source_type": "docker",
                    "target": "portfolio-dev-be-1",
                    "compose_project": "portfolio-dev",
                    "compose_service": "be",
                    "description": "Backend.",
                    "parser_type": "python_json",
                    "normalization_profile": "backend_app",
                    "retention_class": "medium",
                    "command_run": {
                        "enabled": True,
                        "base_command": ["uv", "run", "python", "manage.py"],
                        "cwd": "/app",
                    },
                }
            ],
        }
    )

    result = _landingpage_django_command_run_from_manifest(
        ProjectManifestContext(manifest=manifest, project_name="landingpage")
    )

    assert result == CommandRunTarget(
        container_name="portfolio-dev-be-1",
        base_command=("uv", "run", "python", "manage.py"),
        cwd="/app",
    )
