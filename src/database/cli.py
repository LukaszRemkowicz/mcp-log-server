"""Small project command aliases for database migrations."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence

INIT_DB_REQUIRED_MESSAGES = (
    "You need to run `aerich init-db` first",
    "You may need to run `aerich init-db` first",
)


def _database_env() -> dict[str, str]:
    """Return env with local migration defaults for host-side commands."""

    env = os.environ.copy()
    env.setdefault("DATABASE_HOST", "127.0.0.1")
    env.setdefault("DATABASE_PORT", env.get("DATABASE_PORT_HOST", "5437"))
    return env


def _run_aerich(
    args: Sequence[str],
    *,
    capture_output: bool = False,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run Aerich with the given arguments."""

    return subprocess.run(
        ["aerich", *args],
        capture_output=capture_output,
        check=check,
        env=_database_env(),
        text=True,
    )


def _replay_output(result: subprocess.CompletedProcess[str]) -> None:
    """Write captured subprocess output back to this process."""

    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)


def _replay_error(error: subprocess.CalledProcessError) -> None:
    """Write captured subprocess error output back to this process."""

    if error.stdout:
        sys.stdout.write(str(error.stdout))
    if error.stderr:
        sys.stderr.write(str(error.stderr))


def _run_makemigrations(args: Sequence[str]) -> int:
    """Generate migrations, initializing Aerich first when needed."""

    try:
        result = _run_aerich(["migrate", *args], capture_output=True, check=True)
        _replay_output(result)
        return result.returncode
    except subprocess.CalledProcessError as error:
        output = f"{error.stdout or ''}{error.stderr or ''}"
        if any(message in output for message in INIT_DB_REQUIRED_MESSAGES):
            return _run_aerich(["init-db"]).returncode

        _replay_error(error)
        return error.returncode


def makemigrations() -> None:
    """Generate migration files for current Tortoise models."""

    raise SystemExit(_run_makemigrations(sys.argv[1:]))


def migrate() -> None:
    """Apply committed migration files."""

    raise SystemExit(_run_aerich(["upgrade", *sys.argv[1:]]).returncode)
