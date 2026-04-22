from settings import Settings


def test_settings_defaults_match_phase_zero_a_scaffold() -> None:
    settings = Settings()

    assert settings.environment == "dev"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8001
    assert settings.manifest_path.as_posix() == "manifests/landingpage.json"
