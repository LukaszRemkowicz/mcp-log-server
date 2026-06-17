from __future__ import annotations

from click.utils import strip_ansi
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


def test_slow_analysis_calls_help_describes_operator_command() -> None:
    result = runner.invoke(app, ["slow-analysis-calls", "--help"])
    output = strip_ansi(result.output)

    assert result.exit_code == 0
    assert "Review slow snapshot-analysis MCP calls." in output
    assert "--min-duration" in output
    assert "--tool-name" in output
    assert "--project-name" in output
