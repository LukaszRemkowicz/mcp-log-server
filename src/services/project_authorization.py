"""Project argument authorization helpers for MCP tools."""

from __future__ import annotations

from pydantic import BaseModel

PROJECT_ACCESS_RETRY_TIPS = [
    "Retry with project_name allowed by the current MCP caller project access rules.",
]


class ProjectAuthorizationError(BaseModel):
    """Normalized project-authorization failure for tool-facing callers."""

    message: str
    error_code: str
    retry_tips: list[str]


class ProjectAuthorizationService:
    """Authorize requested project arguments against configured caller projects."""

    @staticmethod
    def _project_access_error(message: str) -> ProjectAuthorizationError:
        """Return one normalized project-access failure."""

        return ProjectAuthorizationError(
            message=message,
            error_code="project_access_mismatch",
            retry_tips=PROJECT_ACCESS_RETRY_TIPS,
        )

    def authorize_project(
        self,
        *,
        allowed_projects: set[str] | frozenset[str],
        requested_project_name: str | None,
    ) -> str | ProjectAuthorizationError:
        """Authorize one requested project name against caller allowed projects."""

        if not requested_project_name:
            return self._project_access_error("project_name is required.")

        project_name = self._normalize_requested_project_name(requested_project_name)
        if isinstance(project_name, ProjectAuthorizationError):
            return project_name
        if project_name not in allowed_projects:
            return self._project_access_error(
                "Requested project is not allowed by the authenticated caller."
            )
        return project_name

    def authorize_projects(
        self,
        *,
        allowed_projects: set[str] | frozenset[str],
        requested_project_names: list[str] | None,
    ) -> list[str] | ProjectAuthorizationError:
        """Authorize requested project names against caller allowed projects."""

        if not requested_project_names:
            return sorted(allowed_projects)

        normalized_project_names: list[str] = []
        seen_project_names: set[str] = set()
        for raw_project_name in requested_project_names:
            project_name = self._normalize_requested_project_name(str(raw_project_name))
            if isinstance(project_name, ProjectAuthorizationError):
                if project_name.message == "project_name must not be empty.":
                    return self._project_access_error(
                        "project_names must not contain empty values."
                    )
                return project_name
            if project_name not in allowed_projects:
                return self._project_access_error(
                    "Requested project is not allowed by the authenticated caller."
                )
            if project_name not in seen_project_names:
                normalized_project_names.append(project_name)
                seen_project_names.add(project_name)
        return normalized_project_names

    def _normalize_requested_project_name(
        self,
        requested_project_name: str,
    ) -> str | ProjectAuthorizationError:
        """Strip one requested project name and reject empty values."""

        project_name = requested_project_name.strip()
        if not project_name:
            return self._project_access_error("project_name must not be empty.")
        return project_name
