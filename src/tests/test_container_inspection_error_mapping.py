from __future__ import annotations

import pytest
from fastmcp.server.auth import AccessToken

from settings import Settings
from tools.errors import build_container_file_error_details, classify_container_file_error


@pytest.mark.parametrize(
    ("message", "expected_error_code", "expected_retry_tips"),
    [
        (
            "JWT is missing the required project_key claim.",
            "missing_project_key_claim",
            [
                "Retry with a JWT that includes the project_key claim for the monitored project.",
            ],
        ),
        (
            "Requested project is not authorized by the access token.",
            "project_access_mismatch",
            [
                "Retry with project_name equal to the project_key authorized by the current JWT.",
            ],
        ),
        (
            "No manifest file was found for the requested project.",
            "unknown_project",
            [
                "Call list_projects to discover the project_name values currently available.",
                "Retry with one of the listed project names.",
            ],
        ),
        (
            "The loaded manifest project_key does not match the requested project.",
            "manifest_project_mismatch",
            [
                "Verify that the manifest filename and its project_key describe the same project.",
            ],
        ),
        (
            "Requested source_key was not found in the configured manifest.",
            "unknown_container_source_key",
            [
                "Retry with one of the docker source_keys returned by "
                "list_projects for this project.",
            ],
        ),
        (
            "Container inspection is only available for docker sources.",
            "container_source_type_mismatch",
            ["Retry with a docker-backed source_key."],
        ),
        (
            "Container inspection is not enabled for the requested source.",
            "container_inspection_not_enabled",
            [
                "Retry with a source that exposes inspect_path_prefixes in the project manifest.",
            ],
        ),
        (
            "Container inspection path must be an absolute path.",
            "container_path_not_absolute",
            ["Retry with an absolute container path like /app/VERSION."],
        ),
        (
            "Container inspection path may not include parent directory traversal.",
            "container_path_parent_traversal",
            ["Retry with a normalized path inside the allowed source prefix."],
        ),
        (
            "Requested container path is outside the manifest whitelist for the selected source.",
            "container_path_not_allowed",
            [
                "Retry with a path under one of the manifest-approved path "
                "prefixes for this source.",
            ],
        ),
        (
            "Docker Engine API is not available in the current runtime.",
            "docker_api_unavailable",
            ["Retry in a runtime where the Docker socket is mounted and reachable."],
        ),
        (
            "Requested container path /app/missing.py was not found.",
            "container_path_not_found",
            ["Retry with a different path under the allowed source prefixes."],
        ),
        (
            "Something unexpected happened.",
            "container_file_inspection_error",
            ["Review the tool arguments and retry with a valid source_key and path."],
        ),
    ],
)
def test_classify_container_file_error_returns_expected_mapping(
    message: str,
    expected_error_code: str,
    expected_retry_tips: list[str],
) -> None:
    error_code, retry_tips = classify_container_file_error(message)

    assert error_code == expected_error_code
    assert retry_tips == expected_retry_tips


@pytest.mark.parametrize(
    (
        "error_code",
        "requested_project_name",
        "source_key",
        "path",
        "expected_details",
    ),
    [
        (
            "project_access_mismatch",
            "wrong-project",
            "backend",
            "/app/missing.py",
            {
                "requested_project_name": "wrong-project",
                "authorized_project_name": "landingpage",
            },
        ),
        (
            "unknown_project",
            "wrong-project",
            "backend",
            "/app/missing.py",
            {"requested_project_name": "wrong-project"},
        ),
        (
            "manifest_project_mismatch",
            "wrong-project",
            "backend",
            "/app/missing.py",
            {
                "manifests_dir": "/tmp/manifests",
            },
        ),
        (
            "unknown_container_source_key",
            "wrong-project",
            "backend",
            "/app/missing.py",
            {"source_key": "backend"},
        ),
        (
            "container_source_type_mismatch",
            "wrong-project",
            "backend",
            "/app/missing.py",
            {"source_key": "backend"},
        ),
        (
            "container_inspection_not_enabled",
            "wrong-project",
            "backend",
            "/app/missing.py",
            {"source_key": "backend"},
        ),
        (
            "container_path_not_absolute",
            "wrong-project",
            "backend",
            "relative/path.py",
            {"path": "relative/path.py"},
        ),
        (
            "container_path_parent_traversal",
            "wrong-project",
            "backend",
            "/app/../etc/passwd",
            {"path": "/app/../etc/passwd"},
        ),
        (
            "container_path_not_allowed",
            "wrong-project",
            "backend",
            "/etc/passwd",
            {"source_key": "backend", "path": "/etc/passwd"},
        ),
        (
            "container_path_not_found",
            "wrong-project",
            "backend",
            "/app/missing.py",
            {"source_key": "backend", "path": "/app/missing.py"},
        ),
        (
            "docker_api_unavailable",
            "wrong-project",
            "backend",
            "/app/missing.py",
            None,
        ),
        (
            "container_file_inspection_error",
            "wrong-project",
            "backend",
            "/app/missing.py",
            None,
        ),
    ],
)
def test_build_container_file_error_details_returns_expected_details(
    settings_fixture: Settings,
    error_code: str,
    requested_project_name: str,
    source_key: str,
    path: str,
    expected_details: dict[str, str] | None,
) -> None:
    settings = settings_fixture.model_copy(
        update={"MANIFEST_PATH": settings_fixture.MANIFEST_PATH.parent / "landingpage.json"}
    )
    access_token = AccessToken(
        token="codex-dev-token",
        client_id="codex-agent",
        scopes=["container.files.read"],
        claims={"sub": "codex-agent", "project_key": "landingpage"},
    )

    details = build_container_file_error_details(
        error_code=error_code,
        requested_project_name=requested_project_name,
        source_key=source_key,
        path=path,
        access_token=access_token,
        settings=settings,
    )

    if error_code == "manifest_project_mismatch":
        assert details == {"manifests_dir": str(settings_fixture.MANIFEST_PATH.parent)}
    else:
        assert details == expected_details
