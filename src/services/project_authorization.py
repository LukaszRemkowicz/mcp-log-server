"""Project authorization helpers for JWT-backed MCP tool access."""

from __future__ import annotations

from dataclasses import dataclass

from fastmcp.server.auth import AccessToken
from pydantic import BaseModel

PROJECT_ACCESS_RETRY_TIPS = [
    "Retry with project_name allowed by the current JWT project access rules.",
]


class ProjectAuthorizationError(BaseModel):
    """Normalized project-authorization failure for tool-facing callers."""

    message: str
    error_code: str
    retry_tips: list[str]


@dataclass(frozen=True, slots=True)
class ProjectAccessScope:
    """Resolved project-access scope from one JWT."""

    all_projects: bool
    allowed_projects: set[str]


class ProjectAuthorizationService:
    """Authorize JWT-backed callers for project-scoped MCP operations.

    This service converts raw JWT project claims into a small set of explicit
    authorization decisions used by MCP tools:

    - read and validate project access claims from the token
    - normalize one requested project name or many requested project names
    - reject empty or malformed project selections
    - confirm whether the caller may access one concrete project

    The service does not inspect manifests or load snapshot data. Its job is
    only to answer project-access questions from JWT scope plus caller input.
    """

    @staticmethod
    def _missing_project_access_claim_error() -> ProjectAuthorizationError:
        """Return the normalized error for a JWT with no project access claims."""

        return ProjectAuthorizationError(
            message=(
                "Authenticated access token must include allowed_projects, "
                "or projects_access='all'."
            ),
            error_code="missing_project_access_claim",
            retry_tips=[
                ("Retry with a JWT that includes allowed_projects, or projects_access='all'."),
            ],
        )

    @staticmethod
    def _project_access_error(message: str) -> ProjectAuthorizationError:
        """Return one normalized project-access failure."""

        return ProjectAuthorizationError(
            message=message,
            error_code="project_access_mismatch",
            retry_tips=PROJECT_ACCESS_RETRY_TIPS,
        )

    @staticmethod
    def _no_allowed_projects_error() -> ProjectAuthorizationError:
        """Return the normalized error for a JWT that resolves to no projects."""

        return ProjectAuthorizationError(
            message="Authenticated access token is not allowed to access any project.",
            error_code="project_access_mismatch",
            retry_tips=PROJECT_ACCESS_RETRY_TIPS,
        )

    def get_required_project_access_scope_or_error(
        self,
        access_token: AccessToken,
    ) -> ProjectAccessScope | ProjectAuthorizationError:
        """Return one usable project-access scope derived from JWT claims.

        This method is the main entrypoint for turning raw JWT claims into a
        validated project access object. It accepts two valid shapes:

        - `projects_access="all"` meaning the caller may access every project
        - `allowed_projects=[...]` meaning the caller may access only the listed
          project names

        Empty or missing project claims are rejected here so callers do not
        need a second "is this scope empty?" check afterward.
        """

        if access_token.claims.get("projects_access") == "all":
            return ProjectAccessScope(all_projects=True, allowed_projects=set())

        claims: dict[str, object] = access_token.claims
        allowed_projects_claim: object = claims.get("allowed_projects")
        if isinstance(allowed_projects_claim, list):
            project_access_scope = ProjectAccessScope(
                all_projects=False,
                allowed_projects={
                    str(item).strip() for item in allowed_projects_claim if str(item).strip()
                },
            )
            if not project_access_scope.allowed_projects:
                return self._no_allowed_projects_error()
            return project_access_scope

        return self._missing_project_access_claim_error()

    @staticmethod
    def has_project_access(
        project_access_scope: ProjectAccessScope,
        project_name: str,
    ) -> bool:
        """Return whether one resolved project-access scope includes a project."""

        return project_access_scope.all_projects or (
            project_name in project_access_scope.allowed_projects
        )

    def _normalize_requested_project_name(
        self,
        requested_project_name: str,
    ) -> str | ProjectAuthorizationError:
        """Strip one requested project name and reject empty values."""

        project_name: str = requested_project_name.strip()
        if not project_name:
            return self._project_access_error("project_name must not be empty.")
        return project_name

    def _authorize_requested_project_name(
        self,
        project_access_scope: ProjectAccessScope,
        requested_project_name: str,
    ) -> str | ProjectAuthorizationError:
        """Return one normalized requested project name if the JWT allows it.

        This helper combines the two repeated per-project checks used by both
        singular and plural authorization:

        - normalize the raw caller input into one project name
        - verify that the resolved JWT scope allows that project
        """

        project_name: str | ProjectAuthorizationError = self._normalize_requested_project_name(
            requested_project_name
        )
        if isinstance(project_name, ProjectAuthorizationError):
            return project_name
        if not self.has_project_access(project_access_scope, project_name):
            return self._project_access_error(
                "Requested project is not allowed by the authenticated access token."
            )
        return project_name

    def authorize_caller_for_project(
        self,
        access_token: AccessToken,
        requested_project_name: str | None,
    ) -> str | ProjectAuthorizationError:
        """Authorize one explicit project name for one single-project tool call.

        Behavior:

        - validate that the JWT resolves to a usable project-access scope
        - require one explicit `project_name`
        - normalize that value and verify access against the JWT scope

        This method does not infer a project automatically when the caller omits
        `project_name`. Single-project tools must name the project explicitly.
        """

        project_access_scope: ProjectAccessScope | ProjectAuthorizationError = (
            self.get_required_project_access_scope_or_error(access_token)
        )
        if isinstance(project_access_scope, ProjectAuthorizationError):
            return project_access_scope

        if not requested_project_name:
            return self._project_access_error("project_name is required.")

        project_name: str | ProjectAuthorizationError = self._authorize_requested_project_name(
            project_access_scope,
            requested_project_name,
        )
        if isinstance(project_name, ProjectAuthorizationError):
            return project_name
        return project_name

    def authorize_caller_for_projects(
        self,
        access_token: AccessToken,
        *,
        requested_project_names: list[str] | None,
        available_project_names: list[str],
    ) -> list[str] | ProjectAuthorizationError:
        """Authorize project names for one multi-project tool request.

        This method is used by plural flows such as `collect_logs`, where the
        caller may request one project, many projects, or omit the parameter
        entirely.

        Behavior:

        - validate that the JWT resolves to a usable project-access scope
        - when `requested_project_names` is provided, normalize every project,
          verify access, and return a deduplicated list
        - when `requested_project_names` is omitted or empty:
          - if the JWT has explicit `allowed_projects`, return all of them
          - if the JWT has `projects_access="all"`, return all available
            manifest-backed project names
        """
        # 1) Check token project access
        project_access_scope: ProjectAccessScope | ProjectAuthorizationError = (
            self.get_required_project_access_scope_or_error(access_token)
        )
        if isinstance(project_access_scope, ProjectAuthorizationError):
            return project_access_scope

        # 2) If projects in token project access list and no requested_project_names
        if not requested_project_names:
            if project_access_scope.all_projects:
                normalized_available_project_names = sorted(
                    {
                        str(project_name).strip()
                        for project_name in available_project_names
                        if str(project_name).strip()
                    }
                )
                # 2a) Check if any projects to watch.
                if not normalized_available_project_names:
                    return self._project_access_error(
                        "No manifest-backed projects are available in the current codebase."
                    )
                # 2b) If scope "all" return all available projects
                return normalized_available_project_names
            allowed_projects: set[str] = project_access_scope.allowed_projects
            return sorted(allowed_projects)

        # 3) if requested_project_names
        normalized_project_names: list[str] = []
        seen_project_names: set[str] = set()
        for raw_project_name in requested_project_names:
            project_name: str | ProjectAuthorizationError = self._authorize_requested_project_name(
                project_access_scope,
                str(raw_project_name),
            )
            # 3a) check if project is valid string
            if isinstance(project_name, ProjectAuthorizationError):
                if project_name.message == "project_name must not be empty.":
                    return self._project_access_error(
                        "project_names must not contain empty values."
                    )
                return project_name
            if project_name not in seen_project_names:
                normalized_project_names.append(project_name)
                seen_project_names.add(project_name)
        return normalized_project_names
