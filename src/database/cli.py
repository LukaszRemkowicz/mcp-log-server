"""Small project command aliases for database migrations."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

MIGRATIONS_DIR = Path("migrations/models")

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


def _migration_sort_key(path: Path) -> int:
    """Return numeric migration prefix, or 0 when a file is not numbered."""

    prefix = path.stem.split("_", maxsplit=1)[0]
    return int(prefix) if prefix.isdecimal() else 0


def _next_migration_prefix(paths: set[Path]) -> str:
    """Return the next Django-style migration number for committed migrations."""

    latest = max(
        (_migration_sort_key(path) for path in paths),
        default=0,
    )
    return f"{latest + 1:03d}"


def _slugify_migration_suffix(value: str) -> str:
    """Return a stable migration filename suffix from command text."""

    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "update"


def _prepare_makemigrations_args(args: Sequence[str]) -> tuple[list[str], str | None]:
    """Translate project-friendly suffix args into Aerich migrate args."""

    aerich_args: list[str] = []
    suffix: str | None = None
    positional_parts: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--name", "-n"} and index + 1 < len(args):
            suffix = _slugify_migration_suffix(args[index + 1])
            aerich_args.extend([arg, suffix])
            index += 2
            continue
        if arg.startswith("--name="):
            suffix = _slugify_migration_suffix(arg.split("=", maxsplit=1)[1])
            aerich_args.append(f"--name={suffix}")
            index += 1
            continue
        if arg.startswith("-"):
            aerich_args.append(arg)
            index += 1
            continue

        positional_parts.append(arg)
        index += 1

    if positional_parts and suffix is None:
        suffix = _slugify_migration_suffix(" ".join(positional_parts))
        aerich_args.extend(["--name", suffix])

    return aerich_args, suffix


def _normalize_generated_migration_name(before: set[Path], suffix: str | None = None) -> None:
    """Rename a newly generated Aerich migration to the project filename style."""

    generated = [
        path
        for path in MIGRATIONS_DIR.glob("*.py")
        if path not in before and not path.stem[:3].isdecimal()
    ]
    if len(generated) != 1:
        return

    source = generated[0]
    parts = source.stem.split("_")
    generated_suffix = (
        "_".join(parts[2:]) if len(parts) >= 3 and parts[1].isdecimal() else source.stem
    )
    target_suffix = suffix or generated_suffix or "update"
    target = source.with_name(f"{_next_migration_prefix(before)}_{target_suffix}.py")
    source.rename(target)


def _run_makemigrations(args: Sequence[str]) -> int:
    """Generate migrations, initializing Aerich first when needed."""

    aerich_args, suffix = _prepare_makemigrations_args(args)
    before = set(MIGRATIONS_DIR.glob("*.py"))
    try:
        result = _run_aerich(["migrate", *aerich_args], capture_output=True, check=True)
        _normalize_generated_migration_name(before, suffix=suffix)
        _replay_output(result)
        return result.returncode
    except subprocess.CalledProcessError as error:
        output = f"{error.stdout or ''}{error.stderr or ''}"
        if any(message in output for message in INIT_DB_REQUIRED_MESSAGES):
            return _run_aerich(["init-db"]).returncode

        _replay_error(error)
        return error.returncode


def _run_test() -> int:
    """Run the full Docker Compose test container suite."""

    return subprocess.run(["docker", "compose", "run", "--rm", "test"]).returncode


def makemigrations() -> None:
    """Generate migration files for current Tortoise models."""

    raise SystemExit(_run_makemigrations(sys.argv[1:]))


def migrate() -> None:
    """Apply committed migration files."""

    raise SystemExit(_run_aerich(["upgrade", *sys.argv[1:]]).returncode)


def test() -> None:
    """Run tests through the Docker Compose test container."""

    raise SystemExit(_run_test())
