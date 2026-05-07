from __future__ import annotations

from services.project_authorization import ProjectAuthorizationError, ProjectAuthorizationService
from tests.conftest import CustomAccessToken


def test_get_required_project_access_scope_or_error_accepts_all_projects_scope(
    custom_access_token: CustomAccessToken,
) -> None:
    service = ProjectAuthorizationService()

    result = service.get_required_project_access_scope_or_error(
        custom_access_token(
            "test-client", ["logs.collect"], "test-client", {"projects_access": "all"}
        )
    )

    assert not isinstance(result, ProjectAuthorizationError)
    assert result.all_projects is True
    assert result.allowed_projects == set()


def test_get_required_project_access_scope_or_error_normalizes_allowed_projects(
    custom_access_token: CustomAccessToken,
) -> None:
    service = ProjectAuthorizationService()

    result = service.get_required_project_access_scope_or_error(
        custom_access_token(
            "test-client",
            ["logs.collect"],
            "test-client",
            {"allowed_projects": [" landingpage ", "", "shop"]},
        )
    )

    assert not isinstance(result, ProjectAuthorizationError)
    assert result.all_projects is False
    assert result.allowed_projects == {"landingpage", "shop"}


def test_get_required_project_access_scope_or_error_rejects_missing_claims(
    custom_access_token: CustomAccessToken,
) -> None:
    service = ProjectAuthorizationService()

    result = service.get_required_project_access_scope_or_error(
        custom_access_token("test-client", ["logs.collect"], "test-client")
    )

    assert isinstance(result, ProjectAuthorizationError)
    assert result.error_code == "missing_project_access_claim"


def test_get_required_project_access_scope_or_error_rejects_empty_allowed_projects(
    custom_access_token: CustomAccessToken,
) -> None:
    service = ProjectAuthorizationService()

    result = service.get_required_project_access_scope_or_error(
        custom_access_token(
            "test-client",
            ["logs.collect"],
            "test-client",
            {"allowed_projects": ["", "   "]},
        )
    )

    assert isinstance(result, ProjectAuthorizationError)
    assert result.error_code == "project_access_mismatch"
    assert result.message == "Authenticated access token is not allowed to access any project."


def test_has_project_access_checks_membership_and_all_projects(
    custom_access_token: CustomAccessToken,
) -> None:
    service = ProjectAuthorizationService()
    all_scope = service.get_required_project_access_scope_or_error(
        custom_access_token(
            "test-client", ["logs.collect"], "test-client", {"projects_access": "all"}
        )
    )
    limited_scope = service.get_required_project_access_scope_or_error(
        custom_access_token(
            "test-client",
            ["logs.collect"],
            "test-client",
            {"allowed_projects": ["landingpage"]},
        )
    )

    assert not isinstance(all_scope, ProjectAuthorizationError)
    assert not isinstance(limited_scope, ProjectAuthorizationError)
    assert service.has_project_access(all_scope, "anything") is True
    assert service.has_project_access(limited_scope, "landingpage") is True
    assert service.has_project_access(limited_scope, "shop") is False


def test_authorize_caller_for_project_requires_explicit_project_name(
    custom_access_token: CustomAccessToken,
) -> None:
    service = ProjectAuthorizationService()

    result = service.authorize_caller_for_project(
        custom_access_token(
            "test-client",
            ["logs.collect"],
            "test-client",
            {"allowed_projects": ["landingpage"]},
        ),
        None,
    )

    assert isinstance(result, ProjectAuthorizationError)
    assert result.message == "project_name is required."


def test_authorize_caller_for_project_rejects_unauthorized_project(
    custom_access_token: CustomAccessToken,
) -> None:
    service = ProjectAuthorizationService()

    result = service.authorize_caller_for_project(
        custom_access_token(
            "test-client",
            ["logs.collect"],
            "test-client",
            {"allowed_projects": ["landingpage"]},
        ),
        "shop",
    )

    assert isinstance(result, ProjectAuthorizationError)
    assert result.message == "Requested project is not allowed by the authenticated access token."


def test_authorize_caller_for_project_returns_normalized_project_name(
    custom_access_token: CustomAccessToken,
) -> None:
    service = ProjectAuthorizationService()

    result = service.authorize_caller_for_project(
        custom_access_token(
            "test-client",
            ["logs.collect"],
            "test-client",
            {"allowed_projects": ["landingpage"]},
        ),
        " landingpage ",
    )

    assert result == "landingpage"


def test_authorize_caller_for_projects_deduplicates_and_normalizes_requested_projects(
    custom_access_token: CustomAccessToken,
) -> None:
    service = ProjectAuthorizationService()

    result = service.authorize_caller_for_projects(
        custom_access_token(
            "test-client", ["logs.collect"], "test-client", {"projects_access": "all"}
        ),
        requested_project_names=[" landingpage ", "shop", "landingpage"],
        available_project_names=["landingpage", "shop", "other"],
    )

    assert result == ["landingpage", "shop"]


def test_authorize_caller_for_projects_returns_all_allowed_projects_when_omitted(
    custom_access_token: CustomAccessToken,
) -> None:
    service = ProjectAuthorizationService()

    result = service.authorize_caller_for_projects(
        custom_access_token(
            "test-client",
            ["logs.collect"],
            "test-client",
            {"allowed_projects": ["shop", "landingpage"]},
        ),
        requested_project_names=None,
        available_project_names=["landingpage", "shop", "other"],
    )

    assert result == ["landingpage", "shop"]


def test_authorize_caller_for_projects_uses_available_projects_for_all_projects_scope(
    custom_access_token: CustomAccessToken,
) -> None:
    service = ProjectAuthorizationService()

    result = service.authorize_caller_for_projects(
        custom_access_token(
            "test-client", ["logs.collect"], "test-client", {"projects_access": "all"}
        ),
        requested_project_names=[],
        available_project_names=[" shop ", "", "landingpage"],
    )

    assert result == ["landingpage", "shop"]


def test_authorize_caller_for_projects_rejects_empty_available_projects_for_all_scope(
    custom_access_token: CustomAccessToken,
) -> None:
    service = ProjectAuthorizationService()

    result = service.authorize_caller_for_projects(
        custom_access_token(
            "test-client", ["logs.collect"], "test-client", {"projects_access": "all"}
        ),
        requested_project_names=None,
        available_project_names=[],
    )

    assert isinstance(result, ProjectAuthorizationError)
    assert result.message == "No manifest-backed projects are available in the current codebase."


def test_authorize_caller_for_projects_rejects_empty_values_in_requested_list(
    custom_access_token: CustomAccessToken,
) -> None:
    service = ProjectAuthorizationService()

    result = service.authorize_caller_for_projects(
        custom_access_token(
            "test-client", ["logs.collect"], "test-client", {"projects_access": "all"}
        ),
        requested_project_names=["landingpage", "   "],
        available_project_names=["landingpage", "shop"],
    )

    assert isinstance(result, ProjectAuthorizationError)
    assert result.message == "project_names must not contain empty values."
