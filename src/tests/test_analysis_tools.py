from __future__ import annotations

import json

from fastmcp.server.auth import AccessToken

from tests.conftest import FileSourceManifestFactory, override_settings
from tools.analysis import build_incident_bundle, group_errors, suggest_followup_window
from tools.collection import collect_logs


def test_group_errors_groups_repeated_structured_failures(
    tmp_path,
) -> None:
    backend_log_file = tmp_path / "backend.log"
    nginx_log_file = tmp_path / "nginx.log"
    backend_log_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-29T10:00:00Z",
                        "level": "ERROR",
                        "message": "Database connection failed",
                        "request_path": "/api/orders",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-29T10:05:00Z",
                        "level": "ERROR",
                        "message": "Database connection failed",
                        "request_path": "/api/orders",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    nginx_log_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "time_local": "29/Apr/2026:10:10:00 +0000",
                        "status": 404,
                        "request": "GET /admin HTTP/1.1",
                        "message": "request completed",
                    }
                ),
                json.dumps(
                    {
                        "time_local": "29/Apr/2026:10:11:00 +0000",
                        "status": 404,
                        "request": "GET /admin HTTP/1.1",
                        "message": "request completed",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    logs_dir = tmp_path / "collected-logs"
    manifest_path = tmp_path / "landingpage.json"
    manifest_path.write_text(
        json.dumps(
            {
                "project_key": "landingpage",
                "project_summary": "Temporary project for grouped error tests.",
                "sources": [
                    {
                        "source_key": "backend",
                        "source_type": "file",
                        "target": str(backend_log_file),
                        "description": "Backend logs.",
                        "required": True,
                        "parser_type": "plain_text",
                        "normalization_profile": "app_logs",
                        "retention_class": "short",
                        "default_noise_profile": "backend_noise",
                        "inspect_path_prefixes": [],
                    },
                    {
                        "source_key": "nginx",
                        "source_type": "file",
                        "target": str(nginx_log_file),
                        "description": "Nginx logs.",
                        "required": True,
                        "parser_type": "plain_text",
                        "normalization_profile": "web_logs",
                        "retention_class": "short",
                        "default_noise_profile": "web_noise",
                        "inspect_path_prefixes": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path, LOGS_DIR=logs_dir):
        collect_logs(
            project_name="landingpage",
            source_keys=["backend", "nginx"],
            workspace="workflow",
            access_token=token,
        )

        result = group_errors(
            snapshot_id="latest",
            project_name="landingpage",
            source_keys=["backend", "nginx"],
            access_token=token,
        )
    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "group_errors"
    assert payload["searched_source_keys"] == ["backend", "nginx"]
    assert payload["analysis_cautions"] == [
        "Use grouped findings for triage, not as the final incident conclusion.",
        (
            "Confirm timing, clustering, and severity with grep_log_snapshot(...) "
            "or read_log_snapshot_file(...)."
        ),
        (
            "Use the original collection window to judge whether a pattern is "
            "bursty, continuous, or isolated."
        ),
    ]
    assert payload["grouped_error_count"] == 2
    assert payload["matching_line_count"] == 4
    assert payload["truncated"] is False
    assert payload["groups"][0]["category"] == "application_error"
    assert payload["groups"][0]["count"] == 2
    assert payload["groups"][0]["source_keys"] == ["backend"]
    assert payload["groups"][0]["first_timestamp"] == "2026-04-29T10:00:00Z"
    assert payload["groups"][0]["last_timestamp"] == "2026-04-29T10:05:00Z"
    assert payload["groups"][1]["category"] == "http_4xx"
    assert payload["groups"][1]["request_paths"] == ["/admin"]
    assert payload["groups"][1]["first_timestamp"] == "29/Apr/2026:10:10:00 +0000"
    assert payload["groups"][1]["last_timestamp"] == "29/Apr/2026:10:11:00 +0000"


def test_group_errors_missing_snapshot_tells_agent_to_collect_first(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("alpha\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path, LOGS_DIR=logs_dir):
        result = group_errors(
            snapshot_id="latest",
            project_name="landingpage",
            access_token=token,
        )
    payload = result.structured_content
    assert payload is not None

    assert payload["status"] == "error"
    assert payload["error_code"] == "snapshot_not_found"
    assert payload["retry_tips"] == [
        "Run collect_logs first to create a persisted snapshot for this project and window.",
        (
            "Retry with a snapshot_id returned by collect_logs, or use "
            'snapshot_id="latest" for the newest workflow snapshot.'
        ),
    ]


def test_build_incident_bundle_returns_grouped_summary(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-29T11:00:00Z",
                        "level": "ERROR",
                        "message": "Database connection failed",
                        "request_path": "/api/orders",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-29T11:02:00Z",
                        "status_code": 502,
                        "request_path": "/api/orders",
                        "message": "upstream error",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    logs_dir = tmp_path / "collected-logs"
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path, LOGS_DIR=logs_dir):
        collect_logs(
            project_name="landingpage",
            source_keys=["app_file"],
            workspace="workflow",
            access_token=token,
        )

        result = build_incident_bundle(
            snapshot_id="latest",
            project_name="landingpage",
            source_keys=["app_file"],
            access_token=token,
        )
    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "build_incident_bundle"
    assert payload["analysis_cautions"] == [
        "Use grouped findings for triage, not as the final incident conclusion.",
        (
            "Confirm timing, clustering, and severity with grep_log_snapshot(...) "
            "or read_log_snapshot_file(...)."
        ),
        (
            "Use the original collection window to judge whether a pattern is "
            "bursty, continuous, or isolated."
        ),
    ]
    assert payload["grouped_error_count"] == 2
    assert payload["matching_line_count"] == 2
    assert payload["high_severity_group_count"] == 2
    assert payload["medium_severity_group_count"] == 0
    assert payload["source_summaries"][0]["source_key"] == "app_file"
    assert payload["source_summaries"][0]["grouped_error_count"] == 2
    assert payload["source_summaries"][0]["first_timestamp"] == "2026-04-29T11:00:00Z"
    assert payload["source_summaries"][0]["last_timestamp"] == "2026-04-29T11:02:00Z"
    assert payload["top_groups"][0]["first_seen"]["source_key"] == "app_file"
    assert payload["top_groups"][0]["first_timestamp"] == "2026-04-29T11:00:00Z"
    assert payload["suggested_next_steps"]


def test_suggest_followup_window_returns_collect_logs_range() -> None:
    result = suggest_followup_window(
        first_timestamp="2026-04-29T10:00:00Z",
        last_timestamp="2026-04-29T10:05:00Z",
        padding_minutes=5,
    )
    payload = result.structured_content
    assert payload is not None

    assert payload["action"] == "suggest_followup_window"
    assert payload["suggested_since"] == "2026-04-29T09:55:00Z"
    assert payload["suggested_until"] == "2026-04-29T10:10:00Z"
    assert payload["ready_for_collect_logs"] is True
    assert payload["example_collect_logs_arguments"] == {
        "since": "2026-04-29T09:55:00Z",
        "until": "2026-04-29T10:10:00Z",
    }
