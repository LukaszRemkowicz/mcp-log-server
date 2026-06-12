from __future__ import annotations

from pathlib import Path


def test_prod_compose_app_healthcheck_uses_healthz() -> None:
    compose_text = Path("docker-compose.prod.yml").read_text()

    assert "healthcheck:" in compose_text
    assert '"-m",' in compose_text
    assert '"healthcheck",' in compose_text


def test_deploy_uses_docker_health_as_final_health_gate() -> None:
    deploy_script = Path("infra/scripts/release/deploy.sh").read_text()

    assert "Wait for Docker app health" in deploy_script
    assert "Verify authenticated MCP health" not in deploy_script
    assert "tools/list" not in deploy_script
