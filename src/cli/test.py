"""Project test command alias."""

from __future__ import annotations

import os
import subprocess


def _run_test() -> int:
    """Run the full Docker Compose test container suite."""

    env = os.environ.copy()
    env["DATABASE_PORT_HOST"] = "0"
    return subprocess.run(
        ["docker", "compose", "run", "--rm", "--build", "test"],
        env=env,
    ).returncode


def test() -> None:
    """Run tests through the Docker Compose test container."""

    raise SystemExit(_run_test())
