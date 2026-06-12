from __future__ import annotations

from datetime import UTC, datetime

from docker.errors import APIError

from services.docker_log_gateway import DockerLogGateway, DockerLogGatewayError


class FakeContainer:
    def __init__(
        self,
        *,
        name: str,
        created: str,
        labels: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.attrs = {
            "Created": created,
            "Config": {"Labels": labels or {}},
        }


class FakeContainers:
    def __init__(self, containers: list[FakeContainer]) -> None:
        self._containers = containers
        self.list_calls: list[dict[str, object]] = []

    def list(self, *, all: bool, filters: dict[str, object]) -> list[FakeContainer]:
        self.list_calls.append({"all": all, "filters": filters})
        return self._containers


class FakeDockerClient:
    def __init__(self, containers: list[FakeContainer]) -> None:
        self.containers = FakeContainers(containers)


def test_resolve_compose_service_ignores_oneoff_containers() -> None:
    """Service lookup should not choose Compose one-off run containers."""

    oneoff = FakeContainer(
        name="portfolio-stage-be-run-a1b2",
        created="2026-06-08T11:00:00Z",
        labels={"com.docker.compose.oneoff": "True"},
    )
    older_service = FakeContainer(
        name="portfolio-stage-be-1",
        created="2026-06-08T10:30:00Z",
        labels={"com.docker.compose.oneoff": "False"},
    )
    newer_service = FakeContainer(
        name="portfolio-stage-be-2",
        created="2026-06-08T10:45:00Z",
        labels={"com.docker.compose.oneoff": "false"},
    )
    client = FakeDockerClient([oneoff, older_service, newer_service])

    resolved = DockerLogGateway(client=client).resolve_container_by_project_service(
        project_name="portfolio-stage",
        service_name="be",
    )

    assert resolved is not None
    assert resolved.name == "portfolio-stage-be-2"
    assert resolved.created_at == datetime(2026, 6, 8, 10, 45, tzinfo=UTC)
    assert client.containers.list_calls == [
        {
            "all": False,
            "filters": {
                "label": [
                    "com.docker.compose.project=portfolio-stage",
                    "com.docker.compose.service=be",
                ],
            },
        }
    ]


def test_resolve_compose_service_returns_none_when_only_oneoffs_match() -> None:
    client = FakeDockerClient(
        [
            FakeContainer(
                name="portfolio-stage-be-run-a1b2",
                created="2026-06-08T11:00:00Z",
                labels={"com.docker.compose.oneoff": "true"},
            )
        ]
    )

    resolved = DockerLogGateway(client=client).resolve_container_by_project_service(
        project_name="portfolio-stage",
        service_name="be",
    )

    assert resolved is None


def test_resolve_compose_service_maps_docker_query_errors() -> None:
    class FailingContainers:
        def list(self, *, all: bool, filters: dict[str, object]) -> list[FakeContainer]:
            raise APIError("daemon down")

    class FailingClient:
        containers = FailingContainers()

    gateway = DockerLogGateway(client=FailingClient())

    try:
        gateway.resolve_container_by_project_service(
            project_name="portfolio-stage",
            service_name="be",
        )
    except DockerLogGatewayError as error:
        assert error.message == "daemon down"
    else:
        raise AssertionError("Expected DockerLogGatewayError")
