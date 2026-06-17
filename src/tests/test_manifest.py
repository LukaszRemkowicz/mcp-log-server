from pathlib import Path

import pytest
from pydantic import ValidationError

from manifests.loader import load_manifest
from manifests.models import Manifest, SourceDefinition
from services.project_manifest import ProjectManifestService
from tests.conftest import TEST_MANIFESTS_DIR


def test_manifest_loads_into_valid_source_manifest() -> None:
    manifest = load_manifest(TEST_MANIFESTS_DIR / "landingpage.json")

    assert isinstance(manifest, Manifest)
    assert manifest.project_key == "landingpage"
    assert manifest.project_summary == "Landingpage project for analysis tests."
    assert [source.source_key for source in manifest.sources] == [
        "backend",
        "nginx",
        "app_file",
        "app_first",
        "app_second",
        "snapshot_text",
        "traefik",
    ]
    assert manifest.sources[0].source_type == "file"
    assert manifest.sources[0].target == "/app/src/tests/fixtures/logs/landingpage/backend.log"
    assert manifest.sources[0].stream is None


def test_vps_security_manifest_declares_host_security_file_sources() -> None:
    """Verify the host-security manifest uses collectable file sources."""

    manifest = load_manifest(TEST_MANIFESTS_DIR / "vps-security.json")

    assert manifest.project_key == "vps-security"
    assert [source.source_key for source in manifest.sources] == [
        "fail2ban",
        "nginx_access",
        "nginx_runtime",
        "traefik_access",
    ]
    assert {source.source_type for source in manifest.sources} == {"file"}
    assert [source.target for source in manifest.sources] == [
        "/app/src/tests/fixtures/logs/vps-security/fail2ban.log",
        "/app/src/tests/fixtures/logs/vps-security/nginx_access.log",
        "/app/src/tests/fixtures/logs/vps-security/nginx_runtime.log",
        "/app/src/tests/fixtures/logs/vps-security/traefik_access.log",
    ]


def test_container_source_lookup_requires_manifest_source_key() -> None:
    """Verify container detail lookup uses the manifest source key contract."""

    source = SourceDefinition(
        source_key="app",
        source_type="docker",
        target="backend-container",
        description="Backend container.",
        parser_type="plain_text",
        normalization_profile="app",
        retention_class="short",
        inspect_path_prefixes=["/app"],
    )
    manifest = Manifest(
        project_key="portfolio",
        project_summary="Portfolio test project.",
        sources=[source],
    )

    assert ProjectManifestService.get_container_source(manifest, "app") == source
    with pytest.raises(ValueError, match="Requested source_key was not found"):
        ProjectManifestService.get_container_source(manifest, "backend")
    with pytest.raises(ValueError, match="Requested source_key was not found"):
        ProjectManifestService.get_container_source(manifest, "backend-container")


def test_source_definition_does_not_expose_compose_selector_fields() -> None:
    """Verify manifests do not maintain Compose service identity as source metadata."""

    assert "compose_project" not in SourceDefinition.model_fields
    assert "compose_service" not in SourceDefinition.model_fields


def test_manifest_loads_absolute_file_source_target(tmp_path: Path) -> None:
    """Verify production manifests may point at absolute log paths."""

    log_file: Path = tmp_path / "application.log"
    manifest_path: Path = tmp_path / "landingpage.json"
    manifest_path.write_text(
        f"""
        {{
          "project_key": "landingpage",
          "project_summary": "Temporary project.",
          "sources": [
            {{
              "source_key": "app_file",
              "source_type": "file",
              "target": "{log_file}",
              "description": "Temporary file-backed logs.",
              "required": true,
              "parser_type": "plain_text",
              "normalization_profile": "app_logs",
              "retention_class": "short",
              "inspect_path_prefixes": []
            }}
          ]
        }}
        """,
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert manifest.sources[0].target == str(log_file)


def test_source_definition_rejects_dot_segments_in_file_source_target() -> None:
    """Verify source targets cannot use path traversal or current-dir segments."""

    with pytest.raises(ValidationError, match="clean absolute path"):
        SourceDefinition(
            source_key="app_file",
            source_type="file",
            target="../application.log",
            description="Temporary file-backed logs.",
            parser_type="plain_text",
            normalization_profile="app_logs",
            retention_class="short",
        )


@pytest.mark.parametrize(
    "target",
    [
        "",
        "~/application.log",
        "logs//application.log",
        "file:///var/log/application.log",
        "C:/logs/application.log",
        "//server/share/application.log",
        "logs/\x00/application.log",
    ],
)
def test_source_definition_rejects_ambiguous_file_source_target(target: str) -> None:
    """Verify file source targets reject expandable or ambiguous path forms."""

    with pytest.raises(ValidationError, match="clean absolute path"):
        SourceDefinition(
            source_key="app_file",
            source_type="file",
            target=target,
            description="Temporary file-backed logs.",
            parser_type="plain_text",
            normalization_profile="app_logs",
            retention_class="short",
        )


def test_source_definition_accepts_absolute_file_source_target(tmp_path: Path) -> None:
    """Verify production file sources may use absolute paths."""

    source = SourceDefinition(
        source_key="app_file",
        source_type="file",
        target=str(tmp_path / "application.log"),
        description="Temporary file-backed logs.",
        parser_type="plain_text",
        normalization_profile="app_logs",
        retention_class="short",
    )

    assert source.target == str(tmp_path / "application.log")


def test_manifest_rejects_dot_segments_in_file_source_target(tmp_path: Path) -> None:
    """Verify manifest loading rejects file targets with dot segments."""

    manifest_path: Path = tmp_path / "landingpage.json"
    manifest_path.write_text(
        """
        {
          "project_key": "landingpage",
          "project_summary": "Temporary project.",
          "sources": [
            {
              "source_key": "app_file",
              "source_type": "file",
              "target": "logs/./application.log",
              "description": "Temporary file-backed logs.",
              "required": true,
              "parser_type": "plain_text",
              "normalization_profile": "app_logs",
              "retention_class": "short",
              "inspect_path_prefixes": []
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="clean absolute path"):
        load_manifest(manifest_path)
