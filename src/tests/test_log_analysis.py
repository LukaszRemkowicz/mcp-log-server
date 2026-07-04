from __future__ import annotations

from pathlib import Path

from core.types import LogWorkspace
from database.fields import FileReference
from database.schemas import CollectLogsSourceOut
from services.log_analysis import LogAnalysisService
from tools.models import LogSnapshotFilePayload, LogSnapshotMetadata


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


def _plain_source(path: Path, *, source_key: str) -> CollectLogsSourceOut:
    return CollectLogsSourceOut(
        id=1,
        source_key=source_key,
        source_type="file",
        target=path.as_posix(),
        description="plain text security log",
        stream=None,
        parser_type="plain_text",
        normalization_profile="security_events",
        default_noise_profile=None,
        status="collected",
        file=FileReference(name=path.as_posix()),
        line_count=1,
        error=None,
        retry_tips=[],
    )


def _metadata(path: Path) -> LogSnapshotMetadata:
    return LogSnapshotMetadata(
        project_name="landingpage",
        workspace=LogWorkspace.WORKFLOW,
        session_id="phase-16c",
        collected_at="2026-05-18T10:00:00Z",
        files=[
            LogSnapshotFilePayload(
                source_key="nginx",
                source_type="file",
                description="nginx access log",
                target=path.as_posix(),
                stream=None,
                parser_type="json",
                normalization_profile="proxy_access",
                default_noise_profile=None,
                file_name=path.name,
                output_file=path.as_posix(),
                line_count=3,
                byte_count=path.stat().st_size,
            )
        ],
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


def test_proxy_activity_reports_omitted_route_groups_explicitly(tmp_path: Path) -> None:
    """The public proxy payload should expose returned and omitted route counts."""

    log_file = tmp_path / "nginx.log"
    log_file.write_text(
        "\n".join(
            [
                '{"status": 404, "request": "GET /admin HTTP/1.1"}',
                '{"status": 502, "request": "POST /api/orders HTTP/1.1"}',
                '{"status": 301, "request": "GET /old HTTP/1.1"}',
            ]
        ),
        encoding="utf-8",
    )

    payload = LogAnalysisService().inspect_proxy_activity(
        _metadata(log_file),
        sources=[_proxy_source(log_file)],
        requested_source_keys=None,
        max_groups=1,
        requested_project_name="landingpage",
        project_name="landingpage",
    )

    assert payload.truncated is True
    assert payload.returned_route_group_count == 1
    assert payload.distinct_route_group_count == 3
    assert payload.distinct_route_group_count_is_exact is True
    assert payload.omitted_route_group_count == 2
    assert payload.route_groups_omitted is True


def test_proxy_activity_marks_distinct_route_count_as_estimated_after_candidate_overflow(
    tmp_path: Path,
) -> None:
    """When candidate tracking overflows, the distinct group count is a lower bound."""

    log_file = tmp_path / "nginx.log"
    log_file.write_text(
        "\n".join(
            [
                *(
                    f'{{"status": 404, "request": "GET /noise-{index} HTTP/1.1"}}'
                    for index in range(40)
                ),
                '{"status": 502, "request": "POST /late-api HTTP/1.1"}',
                '{"status": 502, "request": "POST /late-api HTTP/1.1"}',
                '{"status": 502, "request": "POST /late-api HTTP/1.1"}',
            ]
        ),
        encoding="utf-8",
    )

    payload = LogAnalysisService().inspect_proxy_activity(
        _metadata(log_file),
        sources=[_proxy_source(log_file)],
        requested_source_keys=None,
        max_groups=1,
        requested_project_name="landingpage",
        project_name="landingpage",
    )

    assert payload.route_groups_omitted is True
    assert payload.distinct_route_group_count_is_exact is False
    assert payload.distinct_route_group_count > payload.returned_route_group_count
    assert payload.top_routes[0].path == "/late-api"
    assert payload.top_routes[0].count == 3


def test_probe_blocking_activity_correlates_crowdsec_bans_before_access_logs(
    tmp_path: Path,
) -> None:
    """CrowdSec runtime ban logs should mark later correlated probe records observed."""

    crowdsec_log = tmp_path / "crowdsec.log"
    crowdsec_log.write_text(
        (
            '2026-07-04T00:06:46.322183975Z time="2026-07-04T02:06:46+02:00" '
            'level=info msg="(localhost/crowdsec) portfolio/http-sensitive-probes '
            'by ip 198.51.100.20 (PL/29314) : 876000h ban on Ip 198.51.100.20" '
            "module=db\n"
        ),
        encoding="utf-8",
    )
    access_log = tmp_path / "traefik.jsonl"
    access_log.write_text(
        "\n".join(
            [
                (
                    '{"time":"2026-07-04T00:05:00Z","ClientHost":"198.51.100.20",'
                    '"RequestHost":"lukaszremkowicz.com","RequestPath":"/.env",'
                    '"DownstreamStatus":403}'
                ),
                (
                    '{"time":"2026-07-04T00:05:01Z","ClientHost":"198.51.100.20",'
                    '"RequestHost":"lukaszremkowicz.com","RequestPath":"/.git/config",'
                    '"DownstreamStatus":403}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    payload = LogAnalysisService().inspect_probe_blocking_activity(
        _metadata(access_log),
        sources=[
            _plain_source(crowdsec_log, source_key="crowdsec_runtime"),
            _proxy_source(access_log, source_key="traefik_access"),
        ],
        requested_source_keys=None,
        requested_project_name="vps-security",
        project_name="vps-security",
    )

    assert payload.observed_ban_ip_count == 1
    assert payload.suspicious_ips[0].ip == "198.51.100.20"
    assert payload.suspicious_ips[0].jail == "portfolio-traefik-probes"
    assert payload.suspicious_ips[0].observed_ban is True
    assert payload.suspicious_ips[0].ban_count == 1
    assert payload.suspicious_ips[0].last_ban_at == "2026-07-04T02:06:46+02:00"


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
