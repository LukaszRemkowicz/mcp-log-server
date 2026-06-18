from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from docker_socket_app.adapters import DockerSdkAdapter


class FakeContainer:
    def __init__(self) -> None:
        self.log_kwargs: dict[str, Any] | None = None

    def logs(self, **kwargs: Any) -> list[bytes]:
        self.log_kwargs = kwargs
        return [b"2026-06-17T21:00:00Z ready\n"]


class FakeContainers:
    def __init__(self, container: FakeContainer) -> None:
        self.container = container

    def get(self, container_name: str) -> FakeContainer:
        return self.container


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
