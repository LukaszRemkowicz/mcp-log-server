"""Tiny internal fail2ban status proxy for production Compose deployments.

The main MCP app intentionally runs as a non-root user. On the VPS, the
fail2ban daemon socket is root-only, so live status checks need a separate
privileged boundary. This proxy runs in its own container, exposes only fixed
status endpoints on the internal Compose network, and never accepts arbitrary
commands from callers.
"""

from __future__ import annotations

import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from conf import settings


def _run_fail2ban_status(jail: str | None = None) -> dict[str, object]:
    """Run one allowlisted fail2ban-client status command."""

    command = [
        settings.FAIL2BAN_CLIENT_COMMAND,
        "-s",
        str(settings.FAIL2BAN_SOCKET_PATH),
        "status",
    ]
    if jail is not None:
        if jail not in settings.FAIL2BAN_JAILS:
            return {
                "returncode": 2,
                "stdout": "",
                "stderr": f"Fail2ban jail is not allowlisted: {jail}",
            }
        command.append(jail)

    completed = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        timeout=settings.FAIL2BAN_COMMAND_TIMEOUT_SECONDS,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


class Fail2banProxyHandler(BaseHTTPRequestHandler):
    """Serve fixed fail2ban status endpoints as JSON."""

    server_version = "mcp-fail2ban-proxy/1.0"

    def do_GET(self) -> None:  # noqa: N802
        """Handle one fixed status request."""

        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json({"status": "ok"})
            return
        if parsed.path == "/status":
            self._send_json(_run_fail2ban_status())
            return
        if parsed.path.startswith("/status/"):
            jail = unquote(parsed.path.removeprefix("/status/"))
            self._send_json(_run_fail2ban_status(jail))
            return

        self._send_json(
            {"returncode": 2, "stdout": "", "stderr": "Unknown proxy endpoint."},
            status=404,
        )

    def log_message(self, format: str, *args: object) -> None:
        """Silence default access logs; MCP logs tool-level results already."""

    def _send_json(self, payload: dict[str, object], *, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    """Run the internal fail2ban proxy HTTP server."""

    host = settings.FAIL2BAN_PROXY_HOST
    port = settings.FAIL2BAN_PROXY_PORT
    server = ThreadingHTTPServer((host, port), Fail2banProxyHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
