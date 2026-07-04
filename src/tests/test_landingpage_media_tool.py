from __future__ import annotations

import pytest

from exceptions import DockerSocketGatewayError
from services.landingpage_django import CommandRunTarget, LandingpageMediaInventory
from tools.landingpage_django import inspect_landingpage_media_inventory


@pytest.mark.asyncio
async def test_inspect_landingpage_media_inventory_returns_connector_report(mocker) -> None:
    mocker.patch(
        "decorators._get_allowed_projects",
        return_value=frozenset({"landingpage"}),
    )
    inspect = mocker.patch(
        "tools.landingpage_django.landingpage_django_service.inspect_media_inventory",
        return_value=LandingpageMediaInventory(
            report={
                "schema_version": 1,
                "summary": {
                    "db_files_found_on_disk": 2,
                    "disk_files_total": 3,
                    "disk_files_not_referenced_in_db": 1,
                },
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

    result = await inspect_landingpage_media_inventory(project_name="landingpage")

    assert inspect.call_count == 1
    inspect.assert_called_once_with(
        command_run=CommandRunTarget(
            container_name="portfolio-dev-be-1",
            base_command=("uv", "run", "python", "manage.py"),
            cwd="/app",
        )
    )
    assert result.structured_content is not None
    assert result.structured_content["action"] == "inspect_landingpage_media_inventory"
    assert result.structured_content["project_name"] == "landingpage"
    assert result.structured_content["connector_status"] == "ok"
    assert result.structured_content["report"]["summary"]["disk_files_not_referenced_in_db"] == 1


@pytest.mark.asyncio
async def test_inspect_landingpage_media_inventory_maps_connector_error(mocker) -> None:
    mocker.patch(
        "decorators._get_allowed_projects",
        return_value=frozenset({"landingpage"}),
    )
    mocker.patch(
        "tools.landingpage_django.landingpage_django_service.inspect_media_inventory",
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

    result = await inspect_landingpage_media_inventory(project_name="landingpage")

    assert result.structured_content is not None
    assert result.structured_content["status"] == "error"
    assert result.structured_content["error_code"] == "socket_app_unavailable"
    assert result.structured_content["project_name"] == "landingpage"


@pytest.mark.asyncio
async def test_inspect_landingpage_media_inventory_rejects_unauthorized_project(
    mocker,
) -> None:
    mocker.patch(
        "decorators._get_allowed_projects",
        return_value=frozenset({"landingpage"}),
    )

    result = await inspect_landingpage_media_inventory(project_name="other")

    assert result.structured_content is not None
    assert result.structured_content["status"] == "error"
    assert result.structured_content["error_code"] == "project_access_mismatch"
