from __future__ import annotations

from services.landingpage_django import CommandRunTarget, LandingpageDjangoService


def test_landingpage_django_service_normalizes_media_inventory_names(mocker) -> None:
    gateway_client = mocker.Mock()
    gateway_client.request.return_value = {
        "schema_version": 1,
        "summary": {
            "discovered_fields": 23,
            "db_references": 327,
            "referenced_files": 319,
            "disk_files": 24047,
            "missing_references": 8,
            "unreferenced_files": 23728,
        },
        "fields": [{"field_path": "astrobin.AstroImage.image"}],
        "field_reference_counts": {"astrobin.AstroImage.image": 300},
        "references": [{"field_path": "astrobin.AstroImage.image", "path": "astro/1.jpg"}],
        "missing_references": [{"field_path": "users.User.avatar", "path": "users/missing.jpg"}],
        "disk_files": [{"path": "astro/1.jpg"}],
        "unreferenced_files": [{"path": "tmp/orphan.jpg"}],
        "delete_candidates": [{"path": "tmp/orphan.jpg"}],
        "warnings": [],
    }

    result = LandingpageDjangoService(gateway_client=gateway_client).inspect_media_inventory(
        command_run=CommandRunTarget(
            container_name="portfolio-dev-be-1",
            base_command=("uv", "run", "python", "manage.py"),
            cwd="/app",
        )
    )

    gateway_client.request.assert_called_once_with(
        "landingpage_django_media_inventory",
        {
            "container_name": "portfolio-dev-be-1",
            "base_command": ["uv", "run", "python", "manage.py"],
            "cwd": "/app",
        },
    )
    assert result.report["summary"] == {
        "scanned_file_fields": 23,
        "db_file_references": 327,
        "db_files_found_on_disk": 319,
        "disk_files_total": 24047,
        "broken_db_file_references": 8,
        "disk_files_not_referenced_in_db": 23728,
    }
    assert result.report["scanned_fields"] == [{"field_path": "astrobin.AstroImage.image"}]
    assert result.report["db_file_reference_counts_by_field"] == {"astrobin.AstroImage.image": 300}
    assert result.report["db_file_references"] == [
        {"field_path": "astrobin.AstroImage.image", "path": "astro/1.jpg"}
    ]
    assert result.report["broken_db_file_references"] == [
        {"field_path": "users.User.avatar", "path": "users/missing.jpg"}
    ]
    assert result.report["disk_file_inventory"] == [{"path": "astro/1.jpg"}]
    assert result.report["disk_files_not_referenced_in_db"] == [{"path": "tmp/orphan.jpg"}]
    assert result.report["review_before_delete"] == [{"path": "tmp/orphan.jpg"}]
