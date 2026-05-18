from __future__ import annotations

from services.project_authorization import ProjectAuthorizationError, ProjectAuthorizationService


def test_authorize_project_requires_explicit_project_name() -> None:
    service = ProjectAuthorizationService()

    result = service.authorize_project(
        allowed_projects=frozenset({"landingpage"}),
        requested_project_name=None,
    )

    assert isinstance(result, ProjectAuthorizationError)
    assert result.message == "project_name is required."


def test_authorize_project_rejects_unauthorized_project() -> None:
    service = ProjectAuthorizationService()

    result = service.authorize_project(
        allowed_projects=frozenset({"landingpage"}),
        requested_project_name="shop",
    )

    assert isinstance(result, ProjectAuthorizationError)
    assert result.message == "Requested project is not allowed by the authenticated caller."


def test_authorize_project_returns_normalized_project_name() -> None:
    service = ProjectAuthorizationService()

    result = service.authorize_project(
        allowed_projects=frozenset({"landingpage"}),
        requested_project_name=" landingpage ",
    )

    assert result == "landingpage"


def test_authorize_projects_deduplicates_and_normalizes_requested_projects() -> None:
    service = ProjectAuthorizationService()

    result = service.authorize_projects(
        allowed_projects=frozenset({"landingpage", "shop", "other"}),
        requested_project_names=[" landingpage ", "shop", "landingpage"],
    )

    assert result == ["landingpage", "shop"]


def test_authorize_projects_returns_all_allowed_projects_when_omitted() -> None:
    service = ProjectAuthorizationService()

    result = service.authorize_projects(
        allowed_projects=frozenset({"shop", "landingpage"}),
        requested_project_names=None,
    )

    assert result == ["landingpage", "shop"]


def test_authorize_projects_rejects_empty_values_in_requested_list() -> None:
    service = ProjectAuthorizationService()

    result = service.authorize_projects(
        allowed_projects=frozenset({"landingpage", "shop"}),
        requested_project_names=["landingpage", "   "],
    )

    assert isinstance(result, ProjectAuthorizationError)
    assert result.message == "project_names must not contain empty values."
