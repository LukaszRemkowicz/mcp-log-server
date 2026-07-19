from __future__ import annotations

import os
from base64 import b64decode
from datetime import UTC, datetime
from typing import Any

import pytest
from docker.errors import DockerException
from requests import exceptions as requests_exceptions

from socket_app.adapters import DockerSdkAdapter, LogTransferPage, LogTransferSpool
from socket_app.exceptions import DockerBackendError


class FakeExecResult:
    def __init__(self, *, exit_code: int, output: bytes) -> None:
        self.exit_code = exit_code
        self.output = output


class FakeContainer:
    def __init__(
        self,
        attrs: dict[str, Any] | None = None,
        *,
        log_chunks: list[bytes] | None = None,
    ) -> None:
        self.attrs = attrs or {}
        self.log_chunks = log_chunks or [b"2026-06-17T21:00:00Z ready\n"]
        self.log_kwargs: dict[str, Any] | None = None
        self.log_call_count = 0
        self.exec_calls: list[list[str]] = []
        self.exec_workdirs: list[str | None] = []

    def logs(self, **kwargs: Any) -> list[bytes]:
        self.log_kwargs = kwargs
        self.log_call_count += 1
        return self.log_chunks

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
    def __init__(
        self,
        container: FakeContainer,
        *,
        ping_result: bool = True,
        ping_error: Exception | None = None,
    ) -> None:
        self.containers = FakeContainers(container)
        self.ping_calls = 0
        self.ping_result = ping_result
        self.ping_error = ping_error

    def ping(self) -> bool:
        self.ping_calls += 1
        if self.ping_error is not None:
            raise self.ping_error
        return self.ping_result


def test_service_health_pings_the_docker_daemon() -> None:
    client = FakeDockerClient(FakeContainer())
    adapter = DockerSdkAdapter(client=client)

    result = adapter.service_health()

    assert result == {"status": "ok", "docker_reachable": True}
    assert client.ping_calls == 1


def test_service_health_rejects_false_docker_ping() -> None:
    adapter = DockerSdkAdapter(client=FakeDockerClient(FakeContainer(), ping_result=False))

    with pytest.raises(DockerBackendError, match="Docker daemon ping failed"):
        adapter.service_health()


def test_service_health_maps_docker_ping_timeout() -> None:
    adapter = DockerSdkAdapter(
        client=FakeDockerClient(FakeContainer(), ping_error=requests_exceptions.Timeout())
    )

    with pytest.raises(DockerBackendError, match="Timed out pinging the Docker daemon"):
        adapter.service_health()


def test_service_health_maps_docker_sdk_failure() -> None:
    adapter = DockerSdkAdapter(
        client=FakeDockerClient(
            FakeContainer(),
            ping_error=DockerException("permission denied"),
        )
    )

    with pytest.raises(DockerBackendError, match="permission denied"):
        adapter.service_health()


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


def test_adapter_uses_injected_log_transfer_spool(tmp_path) -> None:
    container = FakeContainer(log_chunks=[b"0123456789"])
    spool = LogTransferSpool(directory=tmp_path, ttl_seconds=5.0, max_bytes=10)
    adapter = DockerSdkAdapter(client=FakeDockerClient(container), log_transfer_spool=spool)

    page = adapter.container_logs_page(container_name="app", max_bytes=4)

    assert page["transfer_id"]
    assert list(tmp_path.glob("log-transfer-*.spool"))
    assert not hasattr(adapter, "log_transfer_dir")
    assert not hasattr(adapter, "max_log_transfer_bytes")


def test_log_transfer_spool_create_page_returns_typed_page_model(tmp_path) -> None:
    spool = LogTransferSpool(directory=tmp_path, ttl_seconds=5.0, max_bytes=10)
    transfer = spool.create(container_name="app", chunks=[b"0123456789"])

    page = spool.create_page(transfer=transfer, offset=0, byte_limit=4)

    assert isinstance(page, LogTransferPage)
    assert page.transfer_id
    assert page.container_name == "app"
    assert page.content == b"0123"
    assert page.returned_bytes == 4
    assert page.to_payload()["logs_base64"] == "MDEyMw=="
    assert not hasattr(spool, "page")


def test_log_transfer_spool_read_page_continues_existing_transfer(tmp_path) -> None:
    spool = LogTransferSpool(directory=tmp_path, ttl_seconds=5.0, max_bytes=10)
    first = spool.create_page(
        transfer=spool.create(container_name="app", chunks=[b"0123456789"]),
        offset=0,
        byte_limit=4,
    )

    assert isinstance(first.transfer_id, str)
    second = spool.read_page(transfer_id=first.transfer_id, offset=4, byte_limit=4)

    assert second.content == b"4567"
    assert second.next_offset == 8


def test_container_logs_page_returns_lossless_raw_byte_pages(tmp_path) -> None:
    raw_logs = b"A\xff\nB\x00C\r\nlast"
    container = FakeContainer(log_chunks=[raw_logs[:4], raw_logs[4:9], raw_logs[9:]])
    adapter = DockerSdkAdapter(
        client=FakeDockerClient(container),
        log_transfer_spool=LogTransferSpool(
            directory=tmp_path,
            ttl_seconds=300.0,
            max_bytes=256 * 1024 * 1024,
        ),
    )

    first_page = adapter.container_logs_page(container_name="mcp-local-db-1", offset=0, max_bytes=5)
    transfer_id = first_page["transfer_id"]
    assert isinstance(transfer_id, str) and transfer_id
    final_page = adapter.container_logs_page(transfer_id=transfer_id, offset=5, max_bytes=20)

    assert b64decode(first_page["logs_base64"], validate=True) == raw_logs[:5]
    assert first_page["offset"] == 0
    assert first_page["returned_bytes"] == 5
    assert first_page["truncated"] is True
    assert first_page["next_offset"] == 5
    assert b64decode(final_page["logs_base64"], validate=True) == raw_logs[5:]
    assert final_page["transfer_id"] is None
    assert final_page["offset"] == 5
    assert final_page["truncated"] is False
    assert final_page["next_offset"] is None
    assert container.log_call_count == 1
    assert list(tmp_path.iterdir()) == []


def test_container_logs_page_rejects_unknown_transfer_and_wrong_cursor(tmp_path) -> None:
    container = FakeContainer(log_chunks=[b"0123456789"])
    adapter = DockerSdkAdapter(
        client=FakeDockerClient(container),
        log_transfer_spool=LogTransferSpool(
            directory=tmp_path,
            ttl_seconds=300.0,
            max_bytes=256 * 1024 * 1024,
        ),
    )
    first_page = adapter.container_logs_page(container_name="app", max_bytes=4)
    transfer_id = first_page["transfer_id"]
    assert isinstance(transfer_id, str)

    with pytest.raises(DockerBackendError, match="offset"):
        adapter.container_logs_page(transfer_id=transfer_id, offset=3, max_bytes=4)
    with pytest.raises(DockerBackendError, match="transfer"):
        adapter.container_logs_page(transfer_id="unknown", offset=4, max_bytes=4)
    assert container.log_call_count == 1


def test_adapter_startup_removes_expired_orphan_spools(tmp_path) -> None:
    orphan = tmp_path / "log-transfer-orphan.spool"
    orphan.write_bytes(b"abandoned")
    os.utime(orphan, (0, 0))

    DockerSdkAdapter(
        client=FakeDockerClient(FakeContainer()),
        log_transfer_spool=LogTransferSpool(directory=tmp_path, ttl_seconds=5.0, max_bytes=100),
    )

    assert not orphan.exists()


def test_container_logs_page_ttl_cleans_abandoned_transfer(tmp_path) -> None:
    now = [10.0]
    container = FakeContainer(log_chunks=[b"0123456789"])
    adapter = DockerSdkAdapter(
        client=FakeDockerClient(container),
        log_transfer_spool=LogTransferSpool(
            directory=tmp_path,
            ttl_seconds=5.0,
            max_bytes=100,
            clock=lambda: now[0],
        ),
    )
    first_page = adapter.container_logs_page(container_name="app", max_bytes=4)
    transfer_id = first_page["transfer_id"]
    assert isinstance(transfer_id, str)
    assert list(tmp_path.iterdir())

    now[0] = 16.0
    with pytest.raises(DockerBackendError, match="transfer"):
        adapter.container_logs_page(transfer_id=transfer_id, offset=4, max_bytes=4)
    assert list(tmp_path.iterdir()) == []


def test_container_logs_page_rejects_transfer_over_total_byte_cap_and_cleans_spool(
    tmp_path,
) -> None:
    container = FakeContainer(log_chunks=[b"1234", b"5678", b"9"])
    adapter = DockerSdkAdapter(
        client=FakeDockerClient(container),
        log_transfer_spool=LogTransferSpool(directory=tmp_path, ttl_seconds=300.0, max_bytes=8),
    )

    with pytest.raises(DockerBackendError, match="exceeded maximum size of 8 bytes"):
        adapter.container_logs_page(container_name="app", max_bytes=4)

    assert container.log_call_count == 1
    assert list(tmp_path.iterdir()) == []


def test_container_logs_page_accepts_transfer_at_total_byte_cap(tmp_path) -> None:
    raw_logs = b"12345678"
    container = FakeContainer(log_chunks=[raw_logs])
    adapter = DockerSdkAdapter(
        client=FakeDockerClient(container),
        log_transfer_spool=LogTransferSpool(
            directory=tmp_path,
            ttl_seconds=300.0,
            max_bytes=len(raw_logs),
        ),
    )

    page = adapter.container_logs_page(container_name="app", max_bytes=len(raw_logs))

    assert b64decode(page["logs_base64"], validate=True) == raw_logs
    assert page["truncated"] is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("stream", "expected_stdout", "expected_stderr"),
    [
        ("stdout", True, False),
        ("stderr", False, True),
        (None, True, True),
    ],
)
def test_container_logs_page_selects_requested_docker_stream(
    stream: str | None,
    expected_stdout: bool,
    expected_stderr: bool,
) -> None:
    container = FakeContainer()
    adapter = DockerSdkAdapter(client=FakeDockerClient(container))

    adapter.container_logs_page(
        container_name="mcp-local-db-1",
        stream=stream,
    )

    assert container.log_kwargs is not None
    assert container.log_kwargs["stdout"] is expected_stdout
    assert container.log_kwargs["stderr"] is expected_stderr


class StreamSelectingContainer(FakeContainer):
    """Model Docker SDK stream selection with distinguishable payloads."""

    def logs(self, **kwargs: Any) -> list[bytes]:
        self.log_kwargs = kwargs
        chunks: list[bytes] = []
        if kwargs["stdout"]:
            chunks.append(b"stdout-only\n")
        if kwargs["stderr"]:
            chunks.append(b"stderr-only\n")
        return chunks


@pytest.mark.parametrize(
    ("stream", "expected_raw"),
    [
        ("stdout", b"stdout-only\n"),
        ("stderr", b"stderr-only\n"),
        (None, b"stdout-only\nstderr-only\n"),
    ],
)
def test_container_logs_page_returns_only_the_selected_stream_payload(
    stream: str | None,
    expected_raw: bytes,
) -> None:
    container = StreamSelectingContainer()
    adapter = DockerSdkAdapter(client=FakeDockerClient(container))

    result = adapter.container_logs_page(
        container_name="mcp-local-db-1",
        stream=stream,
    )

    assert b64decode(result["logs_base64"], validate=True) == expected_raw


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
