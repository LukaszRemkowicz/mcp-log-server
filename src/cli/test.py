"""Project test command alias."""

import subprocess


def _run_test() -> int:
    """Run the full Docker Compose test container suite."""

    return subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--build",
            "test",
        ],
    ).returncode


def test() -> None:
    """Run tests through the Docker Compose test container."""

    raise SystemExit(_run_test())
