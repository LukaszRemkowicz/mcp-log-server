"""Container liveness probe for Docker Compose healthchecks."""

from __future__ import annotations

import os
import urllib.request


def build_healthcheck_url() -> str:
    """Return the local healthcheck URL for the running MCP process."""

    port = os.environ.get("MCP_PORT", "8001")
    return f"http://127.0.0.1:{port}/healthz"


def main() -> None:
    """Exit successfully only when the local health endpoint responds."""

    urllib.request.urlopen(build_healthcheck_url(), timeout=5).read()


if __name__ == "__main__":
    main()
