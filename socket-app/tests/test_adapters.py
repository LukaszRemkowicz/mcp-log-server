from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from socket_app.adapters import DockerSdkAdapter


class FakeExecResult:
    def __init__(self, *, exit_code: int, output: bytes) -> None:
        self.exit_code = exit_code
        self.output = output


class FakeContainer:
    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        self.attrs = attrs or {}
        self.log_kwargs: dict[str, Any] | None = None
        self.exec_calls: list[list[str]] = []
        self.exec_workdirs: list[str | None] = []

    def logs(self, **kwargs: Any) -> list[bytes]:
        self.log_kwargs = kwargs
        return [b"2026-06-17T21:00:00Z ready\n"]

    def exec_run(
        self,
        command: list[str],
        stdout: bool = True,
        stderr: bool = True,
        demux: bool = False,
        tty: bool = False,
        workdir: str | None = None,
    ) -> FakeExecResult:
        _ = (stdout, stderr, tty)
        self.exec_calls.append(command)
        self.exec_workdirs.append(workdir)
        if demux:
            if "mcp_list_commands" in command:
                return FakeExecResult(exit_code=0, output=(b'{"commands":[]}', b""))
            if "media_inventory" in command:
                return FakeExecResult(exit_code=0, output=(b'{"summary":{"disk_files":1}}', b""))
        return FakeExecResult(exit_code=0, output=f"{' '.join(command)} output".encode())


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


def test_crowdsec_activity_runs_only_fixed_cscli_reads() -> None:
    container = FakeContainer()
    adapter = DockerSdkAdapter(client=FakeDockerClient(container))

    result = adapter.crowdsec_activity(container_name="crowdsec")

    assert container.exec_calls == [
        ["cscli", "decisions", "list"],
        ["cscli", "metrics", "show", "appsec"],
        ["cscli", "bouncers", "list"],
        ["cscli", "alerts", "list"],
        ["cscli", "collections", "list"],
    ]
    assert result["container_name"] == "crowdsec"
    assert result["sections"]["decisions"]["ok"] is True
    assert result["sections"]["appsec_metrics"]["command"] == [
        "cscli",
        "metrics",
        "show",
        "appsec",
    ]


def test_landingpage_django_operations_run_only_fixed_commands() -> None:
    container = FakeContainer()
    adapter = DockerSdkAdapter(client=FakeDockerClient(container))

    commands = adapter.landingpage_django_list_commands(
        container_name="portfolio-dev-be-1",
        base_command=["uv", "run", "python", "manage.py"],
        cwd="/app",
    )
    inventory = adapter.landingpage_django_media_inventory(
        container_name="portfolio-dev-be-1",
        base_command=["uv", "run", "python", "manage.py"],
        cwd="/app",
    )

    assert commands == {"commands": []}
    assert inventory == {"summary": {"disk_files": 1}}
    assert container.exec_calls == [
        ["uv", "run", "python", "manage.py", "mcp_list_commands", "--json"],
        ["uv", "run", "python", "manage.py", "media_inventory", "--json"],
    ]
    assert container.exec_workdirs == ["/app", "/app"]
