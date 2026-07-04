"""Service wrapper for fixed landingpage Django operations through socket-app."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from exceptions import DockerSocketGatewayError
from services.docker_socket_gateway import DockerSocketGatewayClient


@dataclass(frozen=True, slots=True)
class LandingpageDjangoCommands:
    """Raw command discovery report returned by the landingpage connector."""

    report: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LandingpageMediaInventory:
    """Raw media inventory report returned by the landingpage connector."""

    report: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CommandRunTarget:
    """Manifest-selected container and command prefix for fixed socket operations."""

    container_name: str
    base_command: tuple[str, ...]
    cwd: str


class LandingpageDjangoService:
    """Call landingpage Django operations through the generic socket app."""

    def __init__(self, gateway_client: DockerSocketGatewayClient | None = None) -> None:
        self.gateway_client = gateway_client or DockerSocketGatewayClient()

    def list_commands(self, *, command_run: CommandRunTarget) -> LandingpageDjangoCommands:
        """Return command metadata from the connector."""

        try:
            report = self.gateway_client.request(
                "landingpage_django_list_commands",
                {
                    "container_name": command_run.container_name,
                    "base_command": list(command_run.base_command),
                    "cwd": command_run.cwd,
                },
            )
        except DockerSocketGatewayError:
            raise
        return LandingpageDjangoCommands(report=report)

    def inspect_media_inventory(
        self, *, command_run: CommandRunTarget
    ) -> LandingpageMediaInventory:
        """Return one media inventory report from the connector."""

        try:
            report = self.gateway_client.request(
                "landingpage_django_media_inventory",
                {
                    "container_name": command_run.container_name,
                    "base_command": list(command_run.base_command),
                    "cwd": command_run.cwd,
                },
            )
        except DockerSocketGatewayError:
            raise
        return LandingpageMediaInventory(report=_normalize_media_inventory_report(report))


_SUMMARY_KEY_RENAMES = {
    "discovered_fields": "scanned_file_fields",
    "db_references": "db_file_references",
    "referenced_files": "db_files_found_on_disk",
    "disk_files": "disk_files_total",
    "missing_references": "broken_db_file_references",
    "unreferenced_files": "disk_files_not_referenced_in_db",
}

_TOP_LEVEL_KEY_RENAMES = {
    "fields": "scanned_fields",
    "field_reference_counts": "db_file_reference_counts_by_field",
    "references": "db_file_references",
    "missing_references": "broken_db_file_references",
    "disk_files": "disk_file_inventory",
    "unreferenced_files": "disk_files_not_referenced_in_db",
    "delete_candidates": "review_before_delete",
}


def _normalize_media_inventory_report(report: dict[str, Any]) -> dict[str, Any]:
    """Translate raw landingpage media inventory names into clearer MCP-facing names."""

    normalized = dict(report)
    summary = normalized.get("summary")
    if isinstance(summary, dict):
        normalized["summary"] = {
            _SUMMARY_KEY_RENAMES.get(str(key), str(key)): value for key, value in summary.items()
        }

    for old_key, new_key in _TOP_LEVEL_KEY_RENAMES.items():
        if old_key in normalized:
            normalized[new_key] = normalized.pop(old_key)

    return normalized
