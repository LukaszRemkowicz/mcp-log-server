from pathlib import Path

from manifests import load_manifest
from manifests.models import SourceManifest
from settings import Settings


def test_settings_default_manifest_path_matches_repository_sample() -> None:
    settings = Settings()

    assert settings.manifest_path == Path("manifests/landingpage.json")


def test_repository_manifest_loads_into_valid_source_manifest() -> None:
    manifest = load_manifest("manifests/landingpage.json")

    assert isinstance(manifest, SourceManifest)
    assert manifest.project_key == "landingpage"
    assert [source.source_key for source in manifest.sources] == [
        "nginx",
        "traefik",
        "backend",
        "frontend",
    ]
    assert manifest.sources[0].source_type == "docker"
    assert manifest.sources[0].stream == "stdout"
