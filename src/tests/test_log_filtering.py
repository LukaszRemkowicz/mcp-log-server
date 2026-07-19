from __future__ import annotations

from pathlib import Path

import pytest

from core.types import LogWorkspace
from database.fields import FileReference
from database.schemas import CollectLogsSourceOut
from services.log_filtering import CreateFilteredViewError, LogFilteringService, SourceNoiseContext
from tools.models import LogSnapshotFilePayload, LogSnapshotMetadata


def _metadata(path: Path) -> LogSnapshotMetadata:
    return LogSnapshotMetadata(
        project_name="landingpage",
        workspace=LogWorkspace.SESSION,
        session_id="gentle-river-finds-a8f2",
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
                default_noise_profile="proxy_noise",
                file_name=path.name,
                output_file=path.as_posix(),
                line_count=3,
                byte_count=path.stat().st_size,
            )
        ],
    )


def _source(path: Path, *, source_key: str = "nginx") -> CollectLogsSourceOut:
    return CollectLogsSourceOut(
        id=1,
        source_key=source_key,
        source_type="file",
        target=path.as_posix(),
        description=f"{source_key} access log",
        stream=None,
        parser_type="json",
        normalization_profile="proxy_access",
        default_noise_profile="proxy_noise",
        status="collected",
        file=FileReference(name=path.as_posix()),
        line_count=3,
        error=None,
        retry_tips=[],
    )


def _source_context(source_key: str) -> SourceNoiseContext:
    return SourceNoiseContext(
        source_key=source_key,
        parser_type="json",
        normalization_profile="proxy_access",
        default_noise_profile="proxy_noise",
    )


def test_filtered_view_head_mode_keeps_chronological_lines(tmp_path: Path) -> None:
    """Verify the default filtered view remains the first cleaned lines."""

    log_file = tmp_path / "nginx.log"
    log_file.write_text(
        "\n".join(
            [
                '{"status": 200, "request": "GET /pricing HTTP/1.1"}',
                '{"status": 500, "request": "GET /api/orders HTTP/1.1"}',
                '{"status": 404, "request": "GET /wp-login.php HTTP/1.1"}',
            ]
        ),
        encoding="utf-8",
    )

    payload = LogFilteringService().create_filtered_view(
        _metadata(log_file),
        sources=[_source(log_file)],
        source_contexts={"nginx": _source_context("nginx")},
        source_keys=None,
        max_lines=2,
        requested_project_name="landingpage",
        project_name="landingpage",
        view_mode="head",
        next_step_tips=[],
    )

    assert not isinstance(payload, CreateFilteredViewError)
    assert [item.line_number for item in payload.cleaned_lines] == [1, 2]
    assert payload.view_mode == "head"


def test_filtered_view_errors_mode_prioritizes_incident_lines(tmp_path: Path) -> None:
    """Verify error mode gives agents high-signal lines before ordinary traffic."""

    log_file = tmp_path / "nginx.log"
    log_file.write_text(
        "\n".join(
            [
                '{"status": 200, "request": "GET /pricing HTTP/1.1"}',
                '{"status": 500, "request": "GET /api/orders HTTP/1.1"}',
                '{"status": 404, "request": "GET /wp-login.php HTTP/1.1"}',
            ]
        ),
        encoding="utf-8",
    )

    payload = LogFilteringService().create_filtered_view(
        _metadata(log_file),
        sources=[_source(log_file)],
        source_contexts={"nginx": _source_context("nginx")},
        source_keys=None,
        max_lines=2,
        requested_project_name="landingpage",
        project_name="landingpage",
        view_mode="errors",
        next_step_tips=[],
    )

    assert not isinstance(payload, CreateFilteredViewError)
    assert [item.line_number for item in payload.cleaned_lines] == [2, 3]
    assert payload.view_mode == "errors"


def test_filtered_view_sample_mode_spreads_lines_across_sources(tmp_path: Path) -> None:
    """Verify sample mode avoids one early source dominating the returned view."""

    nginx_file = tmp_path / "nginx.log"
    traefik_file = tmp_path / "traefik.log"
    nginx_file.write_text(
        "\n".join(
            [
                '{"status": 200, "request": "GET /alpha HTTP/1.1"}',
                '{"status": 200, "request": "GET /beta HTTP/1.1"}',
                '{"status": 200, "request": "GET /gamma HTTP/1.1"}',
            ]
        ),
        encoding="utf-8",
    )
    traefik_file.write_text(
        "\n".join(
            [
                '{"DownstreamStatus": 200, "RequestPath": "/one"}',
                '{"DownstreamStatus": 200, "RequestPath": "/two"}',
                '{"DownstreamStatus": 200, "RequestPath": "/three"}',
            ]
        ),
        encoding="utf-8",
    )

    payload = LogFilteringService().create_filtered_view(
        _metadata(nginx_file),
        sources=[
            _source(nginx_file, source_key="nginx"),
            _source(traefik_file, source_key="traefik"),
        ],
        source_contexts={
            "nginx": _source_context("nginx"),
            "traefik": _source_context("traefik"),
        },
        source_keys=None,
        max_lines=4,
        requested_project_name="landingpage",
        project_name="landingpage",
        view_mode="sample",
        next_step_tips=[],
    )

    assert not isinstance(payload, CreateFilteredViewError)
    assert [(item.source_key, item.line_number) for item in payload.cleaned_lines] == [
        ("nginx", 1),
        ("traefik", 1),
        ("nginx", 2),
        ("traefik", 2),
    ]
    assert payload.view_mode == "sample"


@pytest.mark.parametrize(
    ("source_context", "raw_snapshot", "expected_cleaned_lines", "exclusion_reason"),
    [
        (
            SourceNoiseContext(
                source_key="nginx",
                parser_type="nginx_json",
                normalization_profile="proxy_access",
                default_noise_profile="web_noise",
            ),
            (
                b'{"status": 200, "request": "GET /healthz HTTP/1.1"}\n'
                b'{"status": 204, "request": "GET /healthz HTTP/1.1"}\n'
                b'{"status": 400, "request": "GET /healthz HTTP/1.1"}\n'
                b'{"status": 404, "request": "GET /healthz HTTP/1.1"}\n'
                b'{"status": 503, "request": "GET /healthz HTTP/1.1"}\n'
                b'{"request": "GET /healthz HTTP/1.1"}\n'
                b'{"status": 200, "request": "GET /pricing HTTP/1.1"}\n'
            ),
            [
                '{"status": 400, "request": "GET /healthz HTTP/1.1"}',
                '{"status": 404, "request": "GET /healthz HTTP/1.1"}',
                '{"status": 503, "request": "GET /healthz HTTP/1.1"}',
                '{"request": "GET /healthz HTTP/1.1"}',
                '{"status": 200, "request": "GET /pricing HTTP/1.1"}',
            ],
            "health_check_request",
        ),
        (
            SourceNoiseContext(
                source_key="nginx",
                parser_type="traefik_json",
                normalization_profile="proxy_access",
                default_noise_profile="proxy_noise",
            ),
            (
                b'2026-07-19T10:00:00Z {"DownstreamStatus":200,"RequestPath":"/healthz"}\n'
                b'2026-07-19T10:00:01Z {"DownstreamStatus":200,"RequestPath":"/healthz"}\n'
                b'2026-07-19T10:00:02Z {"DownstreamStatus":404,"RequestPath":"/healthz"}\n'
                b'2026-07-19T10:00:02Z {"DownstreamStatus":503,"RequestPath":"/healthz"}\n'
                b'2026-07-19T10:00:02Z {"RequestPath":"/healthz"}\n'
                b'2026-07-19T10:00:03Z {"DownstreamStatus":200,"RequestPath":"/dashboard"}\n'
            ),
            [
                '2026-07-19T10:00:02Z {"DownstreamStatus":404,"RequestPath":"/healthz"}',
                '2026-07-19T10:00:02Z {"DownstreamStatus":503,"RequestPath":"/healthz"}',
                '2026-07-19T10:00:02Z {"RequestPath":"/healthz"}',
                '2026-07-19T10:00:03Z {"DownstreamStatus":200,"RequestPath":"/dashboard"}',
            ],
            "proxy_health_check_request",
        ),
        (
            SourceNoiseContext(
                source_key="nginx",
                parser_type="python_json",
                normalization_profile="backend_app",
                default_noise_profile="backend_noise",
            ),
            (
                b'{"level":"info","status_code":200,"path":"/healthz"}\n'
                b'{"level":"debug","status_code":200,"path":"/healthz"}\n'
                b'{"level":"info","status_code":404,"path":"/healthz"}\n'
                b'{"level":"info","status_code":503,"path":"/healthz"}\n'
                b'{"level":"error","status_code":503,"path":"/healthz"}\n'
                b'{"level":"info","path":"/healthz"}\n'
                b'{"level":"info","message":"worker ready"}\n'
            ),
            [
                '{"level":"info","status_code":404,"path":"/healthz"}',
                '{"level":"info","status_code":503,"path":"/healthz"}',
                '{"level":"error","status_code":503,"path":"/healthz"}',
                '{"level":"info","path":"/healthz"}',
                '{"level":"info","message":"worker ready"}',
            ],
            "application_health_check_log",
        ),
    ],
    ids=("nginx-json", "traefik-json", "backend-json"),
)
def test_filtered_view_removes_successful_healthz_noise_without_mutating_raw_snapshot(
    tmp_path: Path,
    source_context: SourceNoiseContext,
    raw_snapshot: bytes,
    expected_cleaned_lines: list[str],
    exclusion_reason: str,
) -> None:
    """Verify health noise filtering derives a view without rewriting raw logs."""

    log_file = tmp_path / "raw.log"
    log_file.write_bytes(raw_snapshot)

    payload = LogFilteringService().create_filtered_view(
        _metadata(log_file),
        sources=[_source(log_file)],
        source_contexts={"nginx": source_context},
        source_keys=None,
        max_lines=10,
        requested_project_name="landingpage",
        project_name="landingpage",
        view_mode="head",
        next_step_tips=[],
    )

    assert not isinstance(payload, CreateFilteredViewError)
    assert log_file.read_bytes() == raw_snapshot
    assert [item.line for item in payload.cleaned_lines] == expected_cleaned_lines
    assert payload.total_line_count == len(raw_snapshot.splitlines())
    assert payload.kept_line_count == len(expected_cleaned_lines)
    assert payload.excluded_line_count == 2
    assert payload.source_summaries[0].top_exclusion_reasons == [exclusion_reason]
