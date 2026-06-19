from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from docker_socket_app.adapters import DockerSdkAdapter


class FakeContainer:
    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        self.attrs = attrs or {}
        self.log_kwargs: dict[str, Any] | None = None

    def logs(self, **kwargs: Any) -> list[bytes]:
        self.log_kwargs = kwargs
        return [b"2026-06-17T21:00:00Z ready\n"]


class FakeContainers:
    def __init__(self, container: FakeContainer | None = None) -> None:
        self.container = container

    def get(self, container_name: str) -> FakeContainer:
        assert self.container is not None
        return self.container

    def list(self, all: bool = False) -> list[FakeContainer]:  # noqa: A002
        assert self.container is not None
        return [self.container]


class FakeDockerClient:
    def __init__(self, container: FakeContainer) -> None:
        self.containers = FakeContainers(container)


def test_container_logs_converts_json_timestamp_strings_for_docker_sdk() -> None:
    container = FakeContainer()
    adapter = DockerSdkAdapter(client=FakeDockerClient(container))

    result = adapter.container_logs(
        container_name="mcp-local-db-1",
        since="2026-06-17T20:00:00+00:00",
        until="2026-06-17T21:00:00Z",
        tail=10,
    )

    assert result == {
        "container_name": "mcp-local-db-1",
        "logs": ["2026-06-17T21:00:00Z ready"],
        "truncated": False,
    }
    assert container.log_kwargs is not None
    assert container.log_kwargs["since"] == datetime(2026, 6, 17, 20, 0, tzinfo=UTC)
    assert container.log_kwargs["until"] == datetime(2026, 6, 17, 21, 0, tzinfo=UTC)
    assert container.log_kwargs["tail"] == 10


def test_extract_env_vars_exposes_db_shape_without_secret_values() -> None:
    result = DockerSdkAdapter._extract_env_vars(
        [
            "DATABASE_HOST=db",
            "DATABASE_PORT=5432",
            "DATABASE_NAME=app",
            "DATABASE_USER=app_user",
            "DATABASE_PASSWORD=hidden",
            "DATABASE_URL=postgres://user:password@db/app",
            "NODE_ENV=production",
            "CUSTOM_VALUE=not-safe",
        ]
    )

    assert result == [
        {
            "name": "DATABASE_HOST",
            "value": "db",
            "value_redacted": False,
            "secret": False,
        },
        {
            "name": "DATABASE_PORT",
            "value": "5432",
            "value_redacted": False,
            "secret": False,
        },
        {
            "name": "DATABASE_NAME",
            "value": "app",
            "value_redacted": False,
            "secret": False,
        },
        {
            "name": "DATABASE_USER",
            "value": "app_user",
            "value_redacted": False,
            "secret": False,
        },
        {
            "name": "DATABASE_PASSWORD",
            "value": None,
            "value_redacted": True,
            "secret": True,
        },
        {
            "name": "DATABASE_URL",
            "value": None,
            "value_redacted": True,
            "secret": True,
        },
        {
            "name": "NODE_ENV",
            "value": "production",
            "value_redacted": False,
            "secret": False,
        },
        {
            "name": "CUSTOM_VALUE",
            "value": None,
            "value_redacted": True,
            "secret": False,
        },
    ]


def test_traefik_router_tls_inventory_extracts_sanitized_router_labels() -> None:
    container = FakeContainer(
        attrs={
            "Name": "/portfolio-prod-nginx-1",
            "Config": {
                "Labels": {
                    "traefik.enable": "true",
                    "traefik.http.routers.portfolio-prod.rule": "Host(`example.com`)",
                    "traefik.http.routers.portfolio-prod.entrypoints": "websecure",
                    "traefik.http.routers.portfolio-prod.tls": "true",
                    "traefik.http.routers.portfolio-prod.tls.certresolver": "letsencrypt",
                    "traefik.http.routers.portfolio-prod.service": "portfolio-prod",
                    "traefik.http.middlewares.secret.basicauth.users": "user:hash",
                    "unrelated.secret": "hidden",
                }
            },
            "State": {"Running": True, "Status": "running"},
            "Id": "abc123def4567890",
        }
    )
    adapter = DockerSdkAdapter(client=FakeDockerClient(container))

    result = adapter.traefik_router_tls_inventory()

    assert result == {
        "routers": [
            {
                "router_name": "portfolio-prod",
                "container_name": "portfolio-prod-nginx-1",
                "rule": "Host(`example.com`)",
                "entrypoints": ["websecure"],
                "service": "portfolio-prod",
                "tls_enabled": True,
                "cert_resolver": "letsencrypt",
                "certificate_source": "acme_resolver",
            }
        ],
        "truncated": False,
    }
