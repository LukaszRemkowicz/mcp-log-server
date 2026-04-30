from __future__ import annotations

import json
from pathlib import Path

from fastmcp.server.auth import AccessToken

from tests.conftest import FileSourceManifestFactory, override_settings
from tools.collection import collect_logs
from tools.snapshots import grep_log_snapshot, list_log_snapshot_files, read_log_snapshot_file


def test_list_read_and_grep_log_snapshot_use_persisted_workflow_snapshot(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("alpha\nmatch one\nmatch two\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path, LOGS_DIR=logs_dir):
        collect_result = collect_logs(
            project_name="landingpage",
            source_keys=["app_file"],
            workspace="workflow",
            tail_lines=None,
            timestamps=False,
            since=None,
            until=None,
            access_token=token,
        )
        collect_payload = collect_result.structured_content
        assert collect_payload is not None

        list_result = list_log_snapshot_files(
            snapshot_id="latest",
            project_name="landingpage",
            access_token=token,
        )
        list_payload = list_result.structured_content
        assert list_payload is not None

        assert list_payload["action"] == "list_log_snapshot_files"
        assert list_payload["snapshot_id"] == collect_payload["snapshot_id"]
        assert list_payload["files"][0]["source_key"] == "app_file"

        read_result = read_log_snapshot_file(
            snapshot_id="latest",
            source_key="app_file",
            project_name="landingpage",
            max_bytes=5,
            access_token=token,
        )
        read_payload = read_result.structured_content
        assert read_payload is not None

        assert read_payload["action"] == "read_log_snapshot_file"
        assert read_payload["start_line"] == 1
        assert read_payload["line_count"] == 3
        assert read_payload["content"] == "alpha"
        assert read_payload["truncated"] is True
        assert read_payload["file"]["source_key"] == "app_file"

        grep_result = grep_log_snapshot(
            snapshot_id="latest",
            grep="match",
            project_name="landingpage",
            source_keys=["app_file"],
            access_token=token,
        )
        grep_payload = grep_result.structured_content
        assert grep_payload is not None

    assert grep_payload["action"] == "grep_log_snapshot"
    assert grep_payload["match_offset"] == 0
    assert grep_payload["match_limit"] == 100
    assert grep_payload["match_count"] == 2
    assert grep_payload["returned_match_count"] == 2
    assert grep_payload["truncated"] is False
    assert grep_payload["matched_source_keys"] == ["app_file"]
    assert grep_payload["matches"][0]["line"] == "match one"
    assert grep_payload["matches"][0]["line_truncated"] is False
    assert grep_payload["matches"][1]["line"] == "match two"
    assert grep_payload["matches"][1]["line_truncated"] is False


def test_read_log_snapshot_file_reanchors_tampered_metadata_to_snapshot_dir(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("safe content\n", encoding="utf-8")
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("TOP_SECRET\n", encoding="utf-8")
    logs_dir = tmp_path / "collected-logs"
    manifest_path = file_source_manifest_factory.create(target=str(log_file))
    token = AccessToken(
        token="workflow-dev-token",
        client_id="workflow-agent",
        scopes=["logs.collect"],
        claims={"sub": "workflow-agent", "project_key": "landingpage"},
    )

    with override_settings(MANIFEST_PATH=manifest_path, LOGS_DIR=logs_dir):
        collect_result = collect_logs(
            project_name="landingpage",
            source_keys=["app_file"],
            workspace="workflow",
            access_token=token,
        )
        collect_payload = collect_result.structured_content
        assert collect_payload is not None

        metadata_file = Path(collect_payload["metadata_file"])
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        metadata["files"][0]["output_file"] = str(outside_file)
        metadata_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        read_result = read_log_snapshot_file(
            snapshot_id="latest",
            source_key="app_file",
            project_name="landingpage",
            access_token=token,
        )
    read_payload = read_result.structured_content
    assert read_payload is not None
    assert read_payload["content"] == "safe content\n"


def test_grep_log_snapshot_rejects_unknown_source_keys(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("alpha\nmatch one\n", encoding="utf-8")
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

        grep_result = grep_log_snapshot(
            snapshot_id="latest",
            grep="match",
            project_name="landingpage",
            source_keys=["typo"],
            access_token=token,
        )
    grep_payload = grep_result.structured_content
    assert grep_payload is not None
    assert grep_payload["status"] == "error"
    assert grep_payload["error_code"] == "snapshot_source_key_not_found"


def test_grep_log_snapshot_supports_paged_match_windows(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text(
        "match one\nmatch two\nmatch three\nmatch four\n",
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

        grep_result = grep_log_snapshot(
            snapshot_id="latest",
            grep="match",
            project_name="landingpage",
            source_keys=["app_file"],
            match_offset=1,
            match_limit=2,
            access_token=token,
        )
    grep_payload = grep_result.structured_content
    assert grep_payload is not None

    assert grep_payload["match_offset"] == 1
    assert grep_payload["match_limit"] == 2
    assert grep_payload["match_count"] == 4
    assert grep_payload["returned_match_count"] == 2
    assert grep_payload["truncated"] is True
    assert grep_payload["matches"][0]["line"] == "match two"
    assert grep_payload["matches"][1]["line"] == "match three"


def test_grep_log_snapshot_truncates_large_match_lines(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    long_line = "match " + ("x" * 40)
    log_file = tmp_path / "logs.txt"
    log_file.write_text(f"{long_line}\n", encoding="utf-8")
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

        grep_result = grep_log_snapshot(
            snapshot_id="latest",
            grep="match",
            project_name="landingpage",
            source_keys=["app_file"],
            access_token=token,
        )
    grep_payload = grep_result.structured_content
    assert grep_payload is not None

    assert grep_payload["match_count"] == 1
    assert grep_payload["returned_match_count"] == 1
    assert grep_payload["matches"][0]["line_truncated"] is False
    assert grep_payload["matches"][0]["line"] == long_line


def test_grep_log_snapshot_truncates_very_large_match_lines(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    long_line = "match " + ("x" * 4000)
    log_file = tmp_path / "logs.txt"
    log_file.write_text(f"{long_line}\n", encoding="utf-8")
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

        grep_result = grep_log_snapshot(
            snapshot_id="latest",
            grep="match",
            project_name="landingpage",
            source_keys=["app_file"],
            access_token=token,
        )
    grep_payload = grep_result.structured_content
    assert grep_payload is not None

    assert grep_payload["match_count"] == 1
    assert grep_payload["returned_match_count"] == 1
    assert grep_payload["matches"][0]["line_truncated"] is True
    assert grep_payload["matches"][0]["line"] == long_line.encode("utf-8")[:2000].decode(
        "utf-8",
        errors="ignore",
    )


def test_read_log_snapshot_file_supports_line_chunks(
    tmp_path,
    file_source_manifest_factory: FileSourceManifestFactory,
) -> None:
    log_file = tmp_path / "logs.txt"
    log_file.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
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

        read_result = read_log_snapshot_file(
            snapshot_id="latest",
            source_key="app_file",
            project_name="landingpage",
            start_line=2,
            line_count=2,
            max_bytes=100,
            access_token=token,
        )
    read_payload = read_result.structured_content
    assert read_payload is not None
    assert read_payload["start_line"] == 2
    assert read_payload["line_count"] == 2
    assert read_payload["content"] == "two\nthree\n"


def test_grep_log_snapshot_matches_across_multiple_persisted_files(
    tmp_path,
) -> None:
    first_log_file = tmp_path / "first.log"
    second_log_file = tmp_path / "second.log"
    first_log_file.write_text("alpha\nshared match\nomega\n", encoding="utf-8")
    second_log_file.write_text(
        "beta\nshared match two\nshared match three\n",
        encoding="utf-8",
    )
    logs_dir = tmp_path / "collected-logs"
    manifest_path = tmp_path / "landingpage.json"
    manifest_path.write_text(
        json.dumps(
            {
                "project_key": "landingpage",
                "project_summary": (
                    "Temporary landingpage-style project for multi-source grep tests."
                ),
                "sources": [
                    {
                        "source_key": "app_first",
                        "source_type": "file",
                        "target": str(first_log_file),
                        "description": "Temporary first file-backed application logs.",
                        "required": True,
                        "parser_type": "plain_text",
                        "normalization_profile": "app_logs",
                        "retention_class": "short",
                        "default_noise_profile": "app_noise",
                        "inspect_path_prefixes": [],
                    },
                    {
                        "source_key": "app_second",
                        "source_type": "file",
                        "target": str(second_log_file),
                        "description": "Temporary second file-backed application logs.",
                        "required": True,
                        "parser_type": "plain_text",
                        "normalization_profile": "app_logs",
                        "retention_class": "short",
                        "default_noise_profile": "app_noise",
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
        collect_result = collect_logs(
            project_name="landingpage",
            source_keys=["app_first", "app_second"],
            workspace="workflow",
            tail_lines=None,
            timestamps=False,
            since=None,
            until=None,
            access_token=token,
        )
        collect_payload = collect_result.structured_content
        assert collect_payload is not None

        grep_result = grep_log_snapshot(
            snapshot_id="latest",
            grep="shared match",
            project_name="landingpage",
            source_keys=["app_first", "app_second"],
            access_token=token,
        )
    grep_payload = grep_result.structured_content
    assert grep_payload is not None

    assert collect_payload["resolved_source_keys"] == ["app_first", "app_second"]
    assert grep_payload["action"] == "grep_log_snapshot"
    assert grep_payload["grep"] == "shared match"
    assert grep_payload["match_offset"] == 0
    assert grep_payload["match_limit"] == 100
    assert grep_payload["match_count"] == 3
    assert grep_payload["returned_match_count"] == 3
    assert grep_payload["truncated"] is False
    assert grep_payload["matched_source_keys"] == ["app_first", "app_second"]
    assert grep_payload["searched_source_keys"] == ["app_first", "app_second"]
    assert grep_payload["matches"][0]["source_key"] == "app_first"
    assert grep_payload["matches"][0]["line"] == "shared match"
    assert grep_payload["matches"][0]["line_truncated"] is False
    assert grep_payload["matches"][1]["source_key"] == "app_second"
    assert grep_payload["matches"][1]["line"] == "shared match two"
    assert grep_payload["matches"][1]["line_truncated"] is False
    assert grep_payload["matches"][2]["source_key"] == "app_second"
    assert grep_payload["matches"][2]["line"] == "shared match three"
    assert grep_payload["matches"][2]["line_truncated"] is False
