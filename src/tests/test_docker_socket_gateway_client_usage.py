from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.inspection_tools_service import (
    ContainerPathStat,
    InspectionToolsService,
    VpsVolumeInventory,
)
from services.log_collection import LogCollectionService


class FakeSocketGatewayClient:
    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def request(self, operation: str, params: Mapping[str, object]) -> dict[str, Any]:
        self.calls.append((operation, dict(params)))
        return self.responses[operation]


def test_log_collection_service_uses_shared_socket_gateway_client() -> None:
    client = FakeSocketGatewayClient(
        {
            "vps_containers_inventory": {
                "containers": [
                    {
                        "container_name": "backend",
                        "running": True,
                        "created_at": "2026-06-17T12:00:00+00:00",
                        "compose_labels": {
                            "com.docker.compose.project": "portfolio",
                            "com.docker.compose.service": "be",
                            "com.docker.compose.oneoff": "False",
                        },
                    }
                ]
            }
        }
    )

    result = LogCollectionService(
        docker_socket_client=client
    )._resolve_container_by_compose_service(
        compose_project="portfolio",
        compose_service="be",
    )

    assert result == "backend"
    assert client.calls == [("vps_containers_inventory", {})]


def test_inspection_tools_service_stat_uses_shared_socket_gateway_client() -> None:
    client = FakeSocketGatewayClient(
        {
            "container_path_stat": {
                "path": "/app/manage.py",
                "is_dir": False,
                "size": 661,
                "mode": 493,
                "modified_at": "2026-04-02T06:21:49+00:00",
            }
        }
    )

    result = InspectionToolsService(gateway_client=client).stat_container_path(
        "backend-container",
        "/app/manage.py",
    )

    assert result == ContainerPathStat(
        path="/app/manage.py",
        is_dir=False,
        size=661,
        mode=0o755,
        modified_at="2026-04-02T06:21:49+00:00",
    )
    assert client.calls == [
        (
            "container_path_stat",
            {"container_name": "backend-container", "path": "/app/manage.py"},
        )
    ]


def test_inspection_tools_service_volume_inventory_uses_shared_socket_gateway_client() -> None:
    client = FakeSocketGatewayClient(
        {
            "vps_volumes_inventory": {
                "volumes": [
                    {
                        "volume_name": "mcp-local-db-data",
                        "driver": "local",
                        "scope": "local",
                        "created_at": "2026-06-17T12:00:00Z",
                        "compose_labels": {},
                        "option_keys": [],
                        "mountpoint_available": True,
                        "mountpoint_redacted": True,
                        "usage_ref_count": 1,
                        "usage_size_bytes": 1024,
                    }
                ]
            }
        }
    )

    result = InspectionToolsService(gateway_client=client).inspect_vps_volumes(
        name_prefix="mcp-local"
    )

    assert result == [
        VpsVolumeInventory(
            volume_name="mcp-local-db-data",
            driver="local",
            scope="local",
            created_at="2026-06-17T12:00:00Z",
            compose_labels={},
            option_keys=[],
            mountpoint_available=True,
            mountpoint_redacted=True,
            usage_ref_count=1,
            usage_size_bytes=1024,
        )
    ]
    assert client.calls == [
        (
            "vps_volumes_inventory",
            {
                "dangling_only": False,
                "anonymous_only": False,
                "name_prefix": "mcp-local",
            },
        )
    ]
