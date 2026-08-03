from __future__ import annotations

from pathlib import Path

import pytest

from core.types import LogWorkspace
from database.fields import FileReference
from database.schemas import CollectLogsSourceOut
from services.log_analysis import MAX_ANALYSIS_LINE_BYTES, LogAnalysisService
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


def test_group_errors_uses_traefik_request_path_for_repeated_http_events(
    tmp_path: Path,
) -> None:
    """Changing access metadata must not split one repeated Traefik route."""

    log_file = tmp_path / "traefik.jsonl"
    log_file.write_text(
        "\n".join(
            [
                (
                    '{"time":"2026-07-28T10:00:00Z","ClientHost":"198.51.100.10",'
                    '"RequestHost":"example.com","RequestMethod":"GET",'
                    '"RequestPath":"/wp-login.php","DownstreamStatus":404}'
                ),
                (
                    '{"time":"2026-07-28T10:01:00Z","ClientHost":"198.51.100.11",'
                    '"RequestHost":"example.com","RequestMethod":"GET",'
                    '"RequestPath":"/wp-login.php","DownstreamStatus":404}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="traefik_access")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 1
    assert analysis.groups[0].count == 2
    assert analysis.groups[0].request_paths == ["/wp-login.php"]
    assert analysis.groups[0].request_methods == ["GET"]
    assert analysis.groups[0].request_hosts == ["example.com"]
    assert analysis.groups[0].has_explicit_message is False
    assert analysis.groups[0].identity_kind == "http_summary"
    assert analysis.groups[0].semantic_summary == ""
    assert analysis.groups[0].semantic_identity_hash == ""
    assert analysis.groups[0].message_summary == "HTTP 404 GET example.com /wp-login.php"


@pytest.mark.parametrize("normalization_profile", ["proxy_access", "web_logs"])
def test_group_errors_keeps_edge_upstream_and_unknown_5xx_separate(
    tmp_path: Path,
    normalization_profile: str,
) -> None:
    log_file = tmp_path / "traefik-5xx.jsonl"
    log_file.write_text(
        "\n".join(
            [
                (
                    '{"RequestHost":"example.com","RequestMethod":"GET",'
                    '"RequestPath":"/.env","DownstreamStatus":503,'
                    '"OriginStatus":0,"ServiceURL":""}'
                ),
                (
                    '{"RequestHost":"example.com","RequestMethod":"GET",'
                    '"RequestPath":"/.env","DownstreamStatus":503,'
                    '"OriginStatus":503,"ServiceURL":"http://backend:8000"}'
                ),
                (
                    '{"RequestHost":"example.com","RequestMethod":"GET",'
                    '"RequestPath":"/.env","DownstreamStatus":503}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    source = _proxy_source(log_file, source_key="traefik_access").model_copy(
        update={"normalization_profile": normalization_profile}
    )
    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[source],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 3
    assert {group.upstream_attempted for group in analysis.groups} == {None, False, True}


def test_group_errors_extracts_direct_path_from_structured_request(
    tmp_path: Path,
) -> None:
    """A direct request path retains route identity without a method token."""

    log_file = tmp_path / "direct-request.jsonl"
    log_file.write_text(
        '{"status":404,"request":"/orders/123"}',
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="proxy")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 1
    assert analysis.groups[0].request_paths == ["/orders/123"]
    assert analysis.groups[0].message_summary == "HTTP 404 /orders/123"


def test_group_errors_preserves_stripped_custom_method_token(
    tmp_path: Path,
) -> None:
    """Custom HTTP tokens keep exact case and punctuation after stripping."""

    log_file = tmp_path / "custom-methods.jsonl"
    log_file.write_text(
        "\n".join(
            [
                '{"status":404,"request":"MiXeD+Verb /orders HTTP/1.1"}',
                '{"status":404,"method":"  MiXeD+Verb  ","path":"/orders"}',
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="proxy")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 1
    assert analysis.groups[0].count == 2
    assert analysis.groups[0].request_methods == ["MiXeD+Verb"]


def test_group_errors_ignores_volatile_small_numbers_without_messages(
    tmp_path: Path,
) -> None:
    """Message-less events do not split on changing short timestamps or PIDs."""

    log_file = tmp_path / "structured-events.jsonl"
    log_file.write_text(
        "\n".join(
            [
                (
                    '{"level":"ERROR","event":"worker_failed","operation":"sync",'
                    '"error_category":"backend","error_code":"E_CONN",'
                    '"status":"failed","timestamp":1234,"pid":2345}'
                ),
                (
                    '{"level":"ERROR","event":"worker_failed","operation":"sync",'
                    '"error_category":"backend","error_code":"E_CONN",'
                    '"status":"failed","timestamp":1235,"pid":2346}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="worker")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 1
    assert analysis.groups[0].count == 2
    assert analysis.groups[0].message_summary == (
        "event=worker_failed operation=sync error_category=backend error_code=E_CONN status=failed"
    )


def test_group_errors_keeps_message_less_error_codes_distinct(
    tmp_path: Path,
) -> None:
    """Stable error-code identity prevents different failures from merging."""

    log_file = tmp_path / "structured-error-codes.jsonl"
    log_file.write_text(
        "\n".join(
            [
                (
                    '{"level":"ERROR","event":"worker_failed","operation":"sync",'
                    '"error_category":"backend","error_code":"E1001","status":"failed"}'
                ),
                (
                    '{"level":"ERROR","event":"worker_failed","operation":"sync",'
                    '"error_category":"backend","error_code":"E1002","status":"failed"}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="worker")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 2
    assert {group.message_summary for group in analysis.groups} == {
        (
            "event=worker_failed operation=sync error_category=backend "
            "error_code=E1001 status=failed"
        ),
        (
            "event=worker_failed operation=sync error_category=backend "
            "error_code=E1002 status=failed"
        ),
    }


def test_group_errors_keeps_structured_codes_with_identical_messages_distinct(
    tmp_path: Path,
) -> None:
    """Stable structured codes remain exact identity beside explicit messages."""

    log_file = tmp_path / "structured-message-codes.jsonl"
    log_file.write_text(
        "\n".join(
            [
                (
                    '{"level":"ERROR","event":"worker_failed","error_code":"E1001",'
                    '"status":"failed","message":"worker operation failed"}'
                ),
                (
                    '{"level":"ERROR","event":"worker_failed","error_code":"E1002",'
                    '"status":"failed","message":"worker operation failed"}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="worker")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 2
    assert {group.message_summary for group in analysis.groups} == {
        "worker operation failed | event=worker_failed error_code=E1001 status=failed",
        "worker operation failed | event=worker_failed error_code=E1002 status=failed",
    }


def test_group_errors_preserves_exact_long_codes_beside_identical_messages(
    tmp_path: Path,
) -> None:
    """Lossy display normalization must not merge exact structured error codes."""

    log_file = tmp_path / "structured-message-long-codes.jsonl"
    log_file.write_text(
        "\n".join(
            [
                (
                    '{"level":"ERROR","event":"worker_failed",'
                    '"error_code":"ABCDEF123456","status":"failed",'
                    '"message":"worker operation failed"}'
                ),
                (
                    '{"level":"ERROR","event":"worker_failed",'
                    '"error_code":"ABCDEF123457","status":"failed",'
                    '"message":"worker operation failed"}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="worker")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 2
    assert {group.message_summary for group in analysis.groups} == {
        "worker operation failed | event=worker_failed error_code=<id> status=failed"
    }
    assert {group.identity_kind for group in analysis.groups} == {"explicit_message"}
    assert {group.semantic_summary for group in analysis.groups} == {
        "event=worker_failed error_code=<id> status=failed"
    }
    assert {group.semantic_identity_hash for group in analysis.groups} == {
        "d2751135f060286558b4ea05bbf82fb0a9c50ebd7db26ae20ff672d6e403742e",
        "12e5ff0ef8b2b55509be5d7f993ae9275f4fa349d0e874aeb7255f5fc46e962a",
    }


def test_group_errors_keeps_structured_codes_with_same_http_route_distinct(
    tmp_path: Path,
) -> None:
    """Stable structured codes remain exact identity beside an HTTP summary."""

    log_file = tmp_path / "structured-http-codes.jsonl"
    log_file.write_text(
        "\n".join(
            [
                (
                    '{"status":500,"method":"GET","path":"/jobs",'
                    '"event":"job_failed","error_code":"E1001"}'
                ),
                (
                    '{"status":500,"method":"GET","path":"/jobs",'
                    '"event":"job_failed","error_code":"E1002"}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="worker")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 2
    assert {group.message_summary for group in analysis.groups} == {
        "HTTP 500 GET /jobs | event=job_failed error_code=E1001",
        "HTTP 500 GET /jobs | event=job_failed error_code=E1002",
    }


def test_group_errors_preserves_exact_long_codes_beside_same_http_summary(
    tmp_path: Path,
) -> None:
    """HTTP display summaries may be lossy while exact semantic identity stays distinct."""

    log_file = tmp_path / "structured-http-long-codes.jsonl"
    log_file.write_text(
        "\n".join(
            [
                (
                    '{"status":500,"method":"GET","path":"/jobs",'
                    '"event":"job_failed","error_code":"ABCDEF123456"}'
                ),
                (
                    '{"status":500,"method":"GET","path":"/jobs",'
                    '"event":"job_failed","error_code":"ABCDEF123457"}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="worker")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 2
    assert {group.message_summary for group in analysis.groups} == {
        "HTTP 500 GET /jobs | event=job_failed error_code=<id>"
    }
    assert {group.identity_kind for group in analysis.groups} == {"http_summary"}
    assert {group.semantic_summary for group in analysis.groups} == {
        "event=job_failed error_code=<id>"
    }
    assert {group.semantic_identity_hash for group in analysis.groups} == {
        "ae7551d28cf52d972f25925eee8fa14b05b737156fc58d25892535a6acaa0fab",
        "c198b2ca41250fdc457451c1c1a746a9df23d5ded1591a2299274025f75cef89",
    }


def test_group_errors_keeps_explicit_and_synthesized_identity_kinds_distinct(
    tmp_path: Path,
) -> None:
    """Equal display text from different identity kinds must not collide."""

    log_file = tmp_path / "identity-kinds.jsonl"
    log_file.write_text(
        "\n".join(
            [
                ('{"level":"ERROR","message":"event=worker_failed operation=sync"}'),
                ('{"level":"ERROR","event":"worker_failed","operation":"sync"}'),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="worker")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 2
    assert sorted(group.has_explicit_message for group in analysis.groups) == [
        False,
        True,
    ]


def test_group_errors_normalizes_volatile_numbers_in_raw_structured_fallback(
    tmp_path: Path,
) -> None:
    """Unknown message-less shapes retain meaning but ignore volatile numbers."""

    log_file = tmp_path / "raw-fallback.jsonl"
    log_file.write_text(
        "\n".join(
            [
                ('{"level":"ERROR","detail":"worker stopped","timestamp":1234,"pid":2345}'),
                ('{"level":"ERROR","detail":"worker stopped","timestamp":1235,"pid":2346}'),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="worker")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 1
    assert analysis.groups[0].count == 2
    assert "worker stopped" in analysis.groups[0].message_summary


def test_group_errors_ignores_plain_text_timestamp_prefix_for_exact_identity(
    tmp_path: Path,
) -> None:
    """Timestamp metadata must not split repeated copies of one text error."""

    log_file = tmp_path / "worker.log"
    log_file.write_text(
        "\n".join(
            [
                "2026-07-28T10:00:00Z ERROR database unavailable",
                "2026-07-28T10:01:00Z ERROR database unavailable",
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="worker")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 1
    assert analysis.groups[0].count == 2
    assert analysis.groups[0].message_summary == "ERROR database unavailable"
    assert analysis.groups[0].first_timestamp == "2026-07-28T10:00:00Z"
    assert analysis.groups[0].last_timestamp == "2026-07-28T10:01:00Z"


@pytest.mark.parametrize(
    ("first_timestamp", "last_timestamp"),
    [
        ("2026-07-28T10:00:00", "2026-07-28T10:01:00"),
        ("2026-07-28 10:00:00", "2026-07-28 10:01:00"),
        ("2026/07/28 10:00:00", "2026/07/28 10:01:00"),
        ("2026-07-28T10:00:00+0200", "2026-07-28T10:01:00+0200"),
    ],
    ids=["iso-no-zone", "space-iso", "nginx", "compact-offset"],
)
def test_group_errors_strips_supported_plain_text_timestamp_prefixes(
    tmp_path: Path,
    first_timestamp: str,
    last_timestamp: str,
) -> None:
    """Supported runtime timestamps must not split one repeated text error."""

    log_file = tmp_path / "worker.log"
    log_file.write_text(
        "\n".join(
            [
                f"{first_timestamp} ERROR database unavailable",
                f"{last_timestamp} ERROR database unavailable",
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_plain_source(log_file, source_key="worker")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 1
    assert analysis.groups[0].count == 2
    assert analysis.groups[0].message_summary == "ERROR database unavailable"
    assert analysis.groups[0].first_timestamp == first_timestamp
    assert analysis.groups[0].last_timestamp == last_timestamp


def test_group_errors_keeps_http_host_method_and_explicit_message_distinctions(
    tmp_path: Path,
) -> None:
    """Removing host, method, or explicit message from identity would hide causes."""

    log_file = tmp_path / "app.jsonl"
    log_file.write_text(
        "\n".join(
            [
                (
                    '{"level":"ERROR","status":500,"host":"api.example.com",'
                    '"method":"GET","path":"/jobs","message":"upstream timeout"}'
                ),
                (
                    '{"level":"ERROR","status":500,"host":"api.example.com",'
                    '"method":"POST","path":"/jobs","message":"upstream timeout"}'
                ),
                (
                    '{"level":"ERROR","status":500,"host":"admin.example.com",'
                    '"method":"GET","path":"/jobs","message":"upstream timeout"}'
                ),
                (
                    '{"level":"ERROR","status":500,"host":"api.example.com",'
                    '"method":"GET","path":"/jobs","message":"permission denied"}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="app")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 4
    assert sorted(group.count for group in analysis.groups) == [1, 1, 1, 1]
    assert {tuple(group.request_methods) for group in analysis.groups} == {
        ("GET",),
        ("POST",),
    }
    assert {tuple(group.request_hosts) for group in analysis.groups} == {
        ("admin.example.com",),
        ("api.example.com",),
    }
    assert all(group.has_explicit_message for group in analysis.groups)


def test_group_errors_preserves_distinct_semantic_numeric_error_codes(
    tmp_path: Path,
) -> None:
    """Normalizing volatile IDs must not merge different database error codes."""

    log_file = tmp_path / "database.jsonl"
    log_file.write_text(
        "\n".join(
            [
                '{"level":"ERROR","message":"database failure SQLSTATE 23505"}',
                '{"level":"ERROR","message":"database failure SQLSTATE 40001"}',
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="backend")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 2
    assert {group.message_summary for group in analysis.groups} == {
        "database failure SQLSTATE 23505",
        "database failure SQLSTATE 40001",
    }


def test_group_errors_preserves_labeled_long_numeric_error_codes(
    tmp_path: Path,
) -> None:
    """Explicit labeled codes remain available for agent-side semantic grouping."""

    log_file = tmp_path / "worker-codes.jsonl"
    log_file.write_text(
        "\n".join(
            f'{{"level":"ERROR","message":"worker failed with error code {error_code}"}}'
            for error_code in (
                100001,
                100002,
                123456789012,
                123456789013,
                1234567890123,
                1234567890124,
            )
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="worker")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 6
    assert {group.message_summary for group in analysis.groups} == {
        "worker failed with error code 100001",
        "worker failed with error code 100002",
        "worker failed with error code 123456789012",
        "worker failed with error code 123456789013",
        "worker failed with error code <code:bca2b41a2b25>",
        "worker failed with error code <code:a98a32e71c6f>",
    }


def test_group_errors_hashes_long_structured_numeric_error_codes(
    tmp_path: Path,
) -> None:
    """Structured numeric codes use the same bounded deterministic tokens."""

    log_file = tmp_path / "structured-long-codes.jsonl"
    log_file.write_text(
        "\n".join(
            [
                ('{"level":"ERROR","event":"worker_failed","error_code":"1234567890123"}'),
                ('{"level":"ERROR","event":"worker_failed","error_code":"1234567890124"}'),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="worker")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 2
    assert {group.message_summary for group in analysis.groups} == {
        "event=worker_failed error_code=<code:bca2b41a2b25>",
        "event=worker_failed error_code=<code:a98a32e71c6f>",
    }


def test_group_errors_keeps_exact_non_http_message_variants_for_agent_semantics(
    tmp_path: Path,
) -> None:
    """MCP must not irreversibly merge dynamic messages before agent interpretation."""

    log_file = tmp_path / "workers.jsonl"
    log_file.write_text(
        "\n".join(
            [
                ('{"level":"ERROR","message":"job 123e4567-e89b-12d3-a456-426614174000 failed"}'),
                ('{"level":"ERROR","message":"job 123e4567-e89b-12d3-a456-426614174001 failed"}'),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_proxy_source(log_file, source_key="worker")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 2
    assert len({group.fingerprint for group in analysis.groups}) == 2
    assert all(group.has_explicit_message for group in analysis.groups)
    assert {group.message_summary for group in analysis.groups} == {"job <uuid> failed"}


def test_group_errors_bounds_display_summaries_without_merging_exact_identity(
    tmp_path: Path,
) -> None:
    """Large attacker-controlled messages must not create unbounded group payloads."""

    log_file = tmp_path / "large-errors.log"
    shared_prefix = "é" * (MAX_ANALYSIS_LINE_BYTES + 100)
    log_file.write_text(
        "\n".join(
            [
                f'{{"level":"ERROR","message":"failure {shared_prefix} A"}}',
                f'{{"level":"ERROR","message":"failure {shared_prefix} B"}}',
                f'{{"level":"ERROR","detail":"failure {shared_prefix} C"}}',
                f'{{"level":"ERROR","detail":"failure {shared_prefix} D"}}',
                f"ERROR failure {shared_prefix} E",
                f"ERROR failure {shared_prefix} F",
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[_plain_source(log_file, source_key="worker")],
        requested_source_keys=None,
        max_groups=10,
    )

    assert analysis.total_group_count == 6
    assert len({group.fingerprint for group in analysis.groups}) == 6
    assert all(
        len(group.message_summary.encode("utf-8")) <= MAX_ANALYSIS_LINE_BYTES
        for group in analysis.groups
    )


def test_group_errors_supports_deterministic_pages_without_losing_groups(
    tmp_path: Path,
) -> None:
    """Removing offset support would make groups beyond the hard page invisible."""

    log_file = tmp_path / "routes.jsonl"
    log_file.write_text(
        "\n".join(
            f'{{"status":404,"request":"GET /route-{index} HTTP/1.1"}}' for index in range(4)
        ),
        encoding="utf-8",
    )
    service = LogAnalysisService()
    sources = [_proxy_source(log_file, source_key="proxy")]
    full = service.group_snapshot_errors(
        sources=sources,
        requested_source_keys=None,
        max_groups=10,
    )
    try:
        first_page = service.group_snapshot_errors(
            sources=sources,
            requested_source_keys=None,
            max_groups=2,
            offset=0,
        )
        second_page = service.group_snapshot_errors(
            sources=sources,
            requested_source_keys=None,
            max_groups=2,
            offset=2,
        )
    except TypeError:
        pytest.fail("group_snapshot_errors must support deterministic offset pages")

    assert first_page.total_group_count == 4
    assert second_page.total_group_count == 4
    assert {group.fingerprint for group in [*first_page.groups, *second_page.groups]} == {
        group.fingerprint for group in full.groups
    }
    assert {group.fingerprint for group in first_page.groups}.isdisjoint(
        group.fingerprint for group in second_page.groups
    )
    assert full.analysis_complete is True
    assert full.analysis_group_limit >= full.total_group_count


def test_group_errors_stops_at_distinct_group_limit_and_reports_lower_bounds(
    tmp_path: Path,
) -> None:
    """The safety cap must stop before retaining an unbounded third group."""

    first_log = tmp_path / "worker-a.jsonl"
    first_log.write_text(
        "\n".join(
            [
                '{"level":"ERROR","message":"first failure"}',
                '{"level":"ERROR","message":"second failure"}',
                '{"level":"ERROR","message":"first failure"}',
                '{"level":"ERROR","message":"third failure"}',
                '{"level":"ERROR","message":"first failure"}',
            ]
        ),
        encoding="utf-8",
    )
    unread_log = tmp_path / "worker-b.jsonl"
    unread_log.write_text(
        '{"level":"ERROR","message":"unread failure"}\n',
        encoding="utf-8",
    )

    analysis = LogAnalysisService().group_snapshot_errors(
        sources=[
            _plain_source(first_log, source_key="worker-a"),
            _plain_source(unread_log, source_key="worker-b"),
        ],
        requested_source_keys=None,
        max_groups=10,
        analysis_group_limit=2,
    )

    assert analysis.analysis_complete is False
    assert analysis.analysis_group_limit == 2
    assert analysis.total_group_count == 2
    assert analysis.matching_line_count == 4
    assert analysis.searched_source_keys == ["worker-a"]
    assert sorted(group.count for group in analysis.groups) == [1, 2]

    from tools.analysis import _build_group_errors_summary

    summary = _build_group_errors_summary(
        matching_line_count=analysis.matching_line_count,
        total_group_count=analysis.total_group_count,
        groups=analysis.groups,
        analysis_complete=analysis.analysis_complete,
        analysis_group_limit=analysis.analysis_group_limit,
    )
    assert "safety limit of 2 groups" in summary
    assert "at least 4 error-like lines" in summary
    assert "at least 2 groups" in summary


def test_incident_bundle_exposes_incomplete_group_analysis(tmp_path: Path) -> None:
    """Incident consumers must know when grouped totals are lower bounds."""

    log_file = tmp_path / "worker.jsonl"
    log_file.write_text(
        "\n".join(f'{{"level":"ERROR","message":"failure {index}"}}' for index in range(3)),
        encoding="utf-8",
    )

    payload = LogAnalysisService().build_incident_bundle(
        _metadata(log_file),
        sources=[_plain_source(log_file, source_key="worker")],
        requested_source_keys=None,
        max_groups=10,
        analysis_group_limit=2,
        requested_project_name="landingpage",
        project_name="landingpage",
        analysis_cautions=[],
        next_step_tips=[],
    )

    assert payload.analysis_complete is False
    assert payload.analysis_group_limit == 2
    assert payload.grouped_error_count == 2
    assert payload.matching_line_count == 3
    assert any("lower bounds" in tip for tip in payload.next_step_tips)


def test_incident_bundle_totals_include_groups_beyond_display_limit(tmp_path: Path) -> None:
    """Incident totals must describe the analysis, not only displayed top groups."""

    app_log = tmp_path / "app.jsonl"
    app_log.write_text(
        "\n".join(
            [
                '{"level":"ERROR","message":"database unavailable"}',
                '{"level":"ERROR","message":"queue unavailable"}',
            ]
        ),
        encoding="utf-8",
    )
    proxy_log = tmp_path / "proxy.jsonl"
    proxy_log.write_text(
        '{"status":404,"request":"GET /missing HTTP/1.1"}\n',
        encoding="utf-8",
    )

    payload = LogAnalysisService().build_incident_bundle(
        _metadata(app_log),
        sources=[
            _plain_source(app_log, source_key="app"),
            _proxy_source(proxy_log, source_key="proxy"),
        ],
        requested_source_keys=None,
        max_groups=1,
        requested_project_name="landingpage",
        project_name="landingpage",
        analysis_cautions=[],
        next_step_tips=[],
    )

    assert payload.analysis_complete is True
    assert payload.grouped_error_count == 3
    assert len(payload.top_groups) == 1
    assert payload.high_severity_group_count == 2
    assert payload.medium_severity_group_count == 1
    assert payload.low_severity_group_count == 0
    assert {
        summary.source_key: (summary.grouped_error_count, summary.matching_line_count)
        for summary in payload.source_summaries
    } == {"app": (2, 2), "proxy": (1, 1)}


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
                (
                    '{"status": 502, "request": "POST /late-api HTTP/1.1",'
                    '"upstream_addr": "172.18.0.4:8000", "upstream_status": "502"}'
                ),
                (
                    '{"status": 502, "request": "POST /late-api HTTP/1.1",'
                    '"upstream_addr": "172.18.0.4:8000", "upstream_status": "502"}'
                ),
                (
                    '{"status": 502, "request": "POST /late-api HTTP/1.1",'
                    '"upstream_addr": "172.18.0.4:8000", "upstream_status": "502"}'
                ),
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


def test_proxy_activity_promotes_candidate_when_later_event_reaches_upstream(
    tmp_path: Path,
) -> None:
    """Later upstream evidence must protect a retained route during tie eviction."""

    log_file = tmp_path / "mixed-upstream.jsonl"
    other_routes = [
        f'{{"status":404,"request":"GET /noise-{index} HTTP/1.1"}}' for index in range(31)
    ]
    log_file.write_text(
        "\n".join(
            [
                (
                    '{"status":503,"request":"GET /target HTTP/1.1",'
                    '"OriginStatus":0,"ServiceURL":""}'
                ),
                *other_routes,
                (
                    '{"status":503,"request":"GET /target HTTP/1.1",'
                    '"OriginStatus":503,"ServiceURL":"http://backend:8000"}'
                ),
                *other_routes,
                (
                    '{"status":503,"request":"GET /late HTTP/1.1",'
                    '"OriginStatus":503,"ServiceURL":"http://backend:8000"}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService()._analyze_proxy_activity(
        sources=[_proxy_source(log_file)],
        max_groups=1,
    )

    assert analysis.top_routes[0].path == "/target"
    assert analysis.top_routes[0].count == 2
    assert analysis.top_routes[0].is_upstream_error is True


def test_proxy_activity_reports_omitted_route_groups_explicitly(tmp_path: Path) -> None:
    """The public proxy payload should expose returned and omitted route counts."""

    log_file = tmp_path / "nginx.log"
    log_file.write_text(
        "\n".join(
            [
                '{"status": 404, "request": "GET /admin HTTP/1.1"}',
                (
                    '{"status": 502, "request": "POST /api/orders HTTP/1.1",'
                    '"upstream_addr": "172.18.0.4:8000", "upstream_status": "502"}'
                ),
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
                (
                    '{"status": 502, "request": "POST /late-api HTTP/1.1",'
                    '"upstream_addr": "172.18.0.4:8000", "upstream_status": "502"}'
                ),
                (
                    '{"status": 502, "request": "POST /late-api HTTP/1.1",'
                    '"upstream_addr": "172.18.0.4:8000", "upstream_status": "502"}'
                ),
                (
                    '{"status": 502, "request": "POST /late-api HTTP/1.1",'
                    '"upstream_addr": "172.18.0.4:8000", "upstream_status": "502"}'
                ),
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
                    '"DownstreamStatus":502,"OriginStatus":502,'
                    '"RouterName":"portfolio-prod@docker",'
                    '"ServiceName":"portfolio-prod",'
                    '"ServiceURL":"http://172.18.0.4:8080"}'
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


def test_proxy_activity_does_not_treat_pre_upstream_traefik_503_as_app_failure(
    tmp_path: Path,
) -> None:
    """A middleware response has routing metadata but no upstream attempt."""

    log_file = tmp_path / "traefik.log"
    log_file.write_text(
        (
            '{"time":"2026-07-31T10:00:00Z","ClientHost":"198.51.100.20",'
            '"RequestHost":"lukaszremkowicz.com","RequestPath":"/.env",'
            '"DownstreamStatus":503,"OriginStatus":0,'
            '"RouterName":"portfolio-prod@docker",'
            '"ServiceName":"portfolio-prod","ServiceURL":""}\n'
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService()._analyze_proxy_activity(
        sources=[_proxy_source(log_file, source_key="traefik_access")],
        max_groups=5,
    )

    assert analysis.upstream_error_count == 0
    assert analysis.top_routes[0].status_code == 503
    assert analysis.top_routes[0].is_upstream_error is False


def test_proxy_activity_counts_true_false_and_unknown_upstream_states(
    tmp_path: Path,
) -> None:
    """Absent telemetry must remain distinct from an explicit no-attempt event."""

    log_file = tmp_path / "traefik-upstream-states.log"
    log_file.write_text(
        "\n".join(
            [
                ('{"DownstreamStatus":503,"RequestMethod":"GET","RequestPath":"/jobs"}'),
                (
                    '{"DownstreamStatus":503,"RequestMethod":"GET",'
                    '"RequestPath":"/jobs","OriginStatus":null}'
                ),
                (
                    '{"DownstreamStatus":503,"RequestMethod":"GET",'
                    '"RequestPath":"/jobs","OriginStatus":0,"ServiceURL":""}'
                ),
                (
                    '{"DownstreamStatus":503,"RequestMethod":"GET",'
                    '"RequestPath":"/jobs","OriginStatus":503,'
                    '"ServiceURL":"http://backend:8000"}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    analysis = LogAnalysisService()._analyze_proxy_activity(
        sources=[_proxy_source(log_file, source_key="traefik_access")],
        max_groups=5,
    )

    assert analysis.upstream_error_count == 1
    assert len(analysis.top_routes) == 1
    route = analysis.top_routes[0]
    assert route.upstream_attempt_count == 1
    assert route.non_upstream_count == 1
    assert route.unknown_upstream_count == 2
    assert route.is_upstream_error is True


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
                (
                    '{"status": 503, "request": "GET /healthz HTTP/1.1",'
                    '"upstream_addr": "172.18.0.4:8000", "upstream_status": "503"}'
                ),
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
