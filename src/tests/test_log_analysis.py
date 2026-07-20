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


def test_group_errors_keeps_structured_socket_app_failures(tmp_path: Path) -> None:
    """Socket gateway ERROR events remain visible to deterministic grouping."""

    log_file = tmp_path / "socket-app.jsonl"
    log_file.write_text(
        '{"level":"ERROR","event":"socket_request_completed",'
        '"operation":"service_health","ok":false,'
        '"error_category":"docker_backend",'
        '"error_code":"docker_backend_error"}\n',
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="socket_app")],
        requested_source_keys=None,
        max_groups=5,
    )

    assert analysis.matching_line_count == 1
    assert analysis.groups[0].category == "application_error"
    assert analysis.groups[0].severity == "high"
    assert "docker_backend_error" in analysis.groups[0].first_seen.line


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


def test_probe_blocking_activity_reports_current_appsec_policy_and_matching_bans(
    tmp_path: Path,
) -> None:
    """Only appsec/second-probe bans should count as observed policy evidence."""

    crowdsec_log = tmp_path / "crowdsec.log"
    crowdsec_log.write_text(
        (
            '2026-07-04T00:06:46.322183975Z time="2026-07-04T02:06:46+02:00" '
            'level=info msg="(localhost/crowdsec) appsec/second-probe '
            'by ip 198.51.100.20 (PL/29314) : 876000h ban on Ip 198.51.100.20" '
            "module=db\n"
            '2026-07-04T00:07:46.322183975Z time="2026-07-04T02:07:46+02:00" '
            'level=info msg="(localhost/crowdsec) crowdsecurity/ssh-bf '
            'by ip 203.0.113.40 (PL/29314) : 4h ban on Ip 203.0.113.40" '
            "module=db\n"
            '2026-07-04T00:08:46.322183975Z time="2026-07-04T02:08:46+02:00" '
            'level=info msg="(localhost/crowdsec) appsec/second-probe '
            'by ip 192.0.2.50 (PL/29314) : 876000h ban on Ip 192.0.2.50" '
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
                (
                    '{"time":"2026-07-04T00:05:02Z","ClientHost":"203.0.113.40",'
                    '"RequestHost":"lukaszremkowicz.com","RequestPath":"/.env",'
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

    assert payload.policy.model_dump() == {
        "scenario": "appsec/second-probe",
        "maintained_appsec_detection_threshold": 2,
        "detection_window": "1m",
        "ban_duration": "876000h",
        "effective_permanent_ban": True,
    }
    assert payload.observed_appsec_ban_ip_count == 2
    assert [item.model_dump() for item in payload.appsec_bans] == [
        {
            "ip": "192.0.2.50",
            "appsec_ban_count": 1,
            "last_appsec_ban_at": "2026-07-04T02:08:46+02:00",
            "has_suspicious_access_context": False,
        },
        {
            "ip": "198.51.100.20",
            "appsec_ban_count": 1,
            "last_appsec_ban_at": "2026-07-04T02:06:46+02:00",
            "has_suspicious_access_context": True,
        },
    ]
    assert [item.ip for item in payload.suspicious_ips] == [
        "198.51.100.20",
        "203.0.113.40",
    ]
    assert payload.suspicious_ips[0].ip == "198.51.100.20"
    assert payload.suspicious_ips[0].suspicious_access_count == 2
    assert payload.suspicious_ips[0].observed_appsec_ban is True
    assert payload.suspicious_ips[0].appsec_ban_count == 1
    assert payload.suspicious_ips[0].last_appsec_ban_at == "2026-07-04T02:06:46+02:00"
    assert payload.suspicious_ips[1].ip == "203.0.113.40"
    assert payload.suspicious_ips[1].observed_appsec_ban is False
    assert payload.suspicious_ips[1].appsec_ban_count == 0


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


def test_proxy_activity_excludes_only_successful_health_requests_from_derived_metrics(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "nginx-health.jsonl"
    raw_snapshot = (
        "\n".join(
            [
                '{"status": 200, "request": "GET /healthz HTTP/1.1"}',
                '{"status": 204, "request": "GET /ready HTTP/1.1"}',
                '{"status": 404, "request": "GET /healthz HTTP/1.1"}',
                '{"status": 503, "request": "GET /healthz HTTP/1.1"}',
                '{"request": "GET /healthz HTTP/1.1"}',
                '{"status": 200, "request": "GET /api HTTP/1.1"}',
            ]
        )
        + "\n"
    )
    log_file.write_text(raw_snapshot, encoding="utf-8")

    payload = LogAnalysisService().inspect_proxy_activity(
        _metadata(log_file),
        sources=[_proxy_source(log_file)],
        requested_source_keys=None,
        max_groups=10,
        requested_project_name="landingpage",
        project_name="landingpage",
    )

    assert log_file.read_text(encoding="utf-8") == raw_snapshot
    assert payload.total_line_count == 6
    assert payload.parsed_proxy_line_count == 6
    assert payload.excluded_health_check_count == 2
    assert payload.http_status_line_count == 3
    assert payload.upstream_error_count == 1
    assert {(item.status_class, item.count) for item in payload.status_class_counts} == {
        ("2xx", 1),
        ("4xx", 1),
        ("5xx", 1),
    }
    assert {(item.path, item.status_code) for item in payload.top_routes} == {
        ("/api", 200),
        ("/healthz", 404),
        ("/healthz", 503),
    }
