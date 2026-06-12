from __future__ import annotations

from pathlib import Path

from database.fields import FileReference
from database.schemas import CollectLogsSourceOut
from services.log_analysis import LogAnalysisService


def _proxy_source(path: Path, *, source_key: str = "nginx") -> CollectLogsSourceOut:
    return CollectLogsSourceOut(
        id=1,
        source_key=source_key,
        source_type="file",
        target=path.as_posix(),
        description="nginx access log",
        stream=None,
        parser_type="json",
        normalization_profile="proxy_access",
        default_noise_profile=None,
        status="collected",
        file=FileReference(name=path.as_posix()),
        line_count=3,
        error=None,
        retry_tips=[],
    )


def test_group_errors_ignores_info_structured_json_with_error_like_tool_names(
    tmp_path: Path,
) -> None:
    """Structured INFO records should not fall back to plain text keyword matching."""

    log_file = tmp_path / "app.jsonl"
    log_file.write_text(
        "\n".join(
            [
                (
                    '{"level":"INFO","event":"tool_result","tool_name":"group_errors",'
                    '"message":"tool result","payload":{"action":"group_errors"}}'
                ),
                (
                    '{"level":"INFO","event":"tool_call","message":"calling '
                    'build_incident_bundle after group_errors"}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="app_file")],
        requested_source_keys=None,
        max_groups=5,
    )

    assert analysis.matching_line_count == 0
    assert analysis.total_group_count == 0
    assert analysis.groups == []


def test_group_errors_keeps_structured_json_error_level_failures(tmp_path: Path) -> None:
    """Structured ERROR records remain grouped from parsed fields."""

    log_file = tmp_path / "app.jsonl"
    log_file.write_text(
        "\n".join(
            [
                '{"level":"INFO","message":"regular request finished"}',
                '{"level":"ERROR","message":"Database connection failed"}',
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="app_file")],
        requested_source_keys=None,
        max_groups=5,
    )

    assert analysis.matching_line_count == 1
    assert analysis.total_group_count == 1
    assert analysis.groups[0].category == "application_error"
    assert analysis.groups[0].severity == "high"
    assert analysis.groups[0].message_summary == "Database connection failed"


def test_proxy_activity_keeps_late_repeated_upstream_route_when_groups_are_bounded(
    tmp_path: Path,
) -> None:
    """Late repeated upstream errors should not be hidden by early unique routes."""

    log_file = tmp_path / "nginx.log"
    log_file.write_text(
        "\n".join(
            [
                *(
                    f'{{"status": 404, "request": "GET /noise-{index} HTTP/1.1"}}'
                    for index in range(20)
                ),
                '{"status": 502, "request": "POST /late-api HTTP/1.1"}',
                '{"status": 502, "request": "POST /late-api HTTP/1.1"}',
                '{"status": 502, "request": "POST /late-api HTTP/1.1"}',
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService()._analyze_proxy_activity(
        sources=[_proxy_source(log_file)],
        max_groups=1,
    )

    assert len(analysis.top_routes) == 1
    assert analysis.total_route_group_count > 1
    assert analysis.top_routes[0].path == "/late-api"
    assert analysis.top_routes[0].status_code == 502
    assert analysis.top_routes[0].count == 3
    assert analysis.top_routes[0].is_upstream_error is True


def test_proxy_activity_maps_traefik_downstream_status_fields(tmp_path: Path) -> None:
    """Verify Traefik access logs contribute status classes and route groups."""

    log_file = tmp_path / "traefik.log"
    log_file.write_text(
        "\n".join(
            [
                (
                    '{"time":"2026-05-18T09:52:41Z","ClientHost":"10.0.0.12",'
                    '"RequestHost":"lukaszremkowicz.com","RequestPath":"/travel",'
                    '"DownstreamStatus":502,"RouterName":"portfolio-prod@docker",'
                    '"ServiceName":"portfolio-prod"}'
                ),
                (
                    '{"time":"2026-05-18T09:54:03Z","ClientHost":"198.51.100.99",'
                    '"RequestHost":"lukaszremkowicz.com","RequestPath":"/wp-login.php",'
                    '"DownstreamStatus":403,"RouterName":"portfolio-prod@docker",'
                    '"ServiceName":"portfolio-prod"}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService()._analyze_proxy_activity(
        sources=[_proxy_source(log_file, source_key="traefik_access")],
        max_groups=5,
    )

    assert analysis.total_line_count == 2
    assert analysis.parsed_proxy_line_count == 2
    assert analysis.http_status_line_count == 2
    assert analysis.upstream_error_count == 1
    assert [item.model_dump(mode="json") for item in analysis.status_class_counts] == [
        {"status_class": "4xx", "count": 1},
        {"status_class": "5xx", "count": 1},
    ]
    assert analysis.top_routes[0].path == "/travel"
    assert analysis.top_routes[0].host == "lukaszremkowicz.com"
    assert analysis.top_routes[0].status_code == 502
    assert analysis.top_routes[0].is_upstream_error is True
