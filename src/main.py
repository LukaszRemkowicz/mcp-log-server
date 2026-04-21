"""CLI entrypoint for local development."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import build_health_payload, create_application, service_status
from auth.providers.allow_all import AllowAllAuthProvider
from settings import get_settings


def main() -> None:
    """Run the HTTP service."""

    settings = get_settings()
    auth_provider = AllowAllAuthProvider()
    create_application(settings=settings, auth_provider=auth_provider)
    serve_http()


def serve_http() -> None:
    """Serve a small HTTP status endpoint for local validation."""

    settings = get_settings()
    auth_provider = AllowAllAuthProvider()
    auth_context = auth_provider.authenticate()
    status_payload = service_status(settings, auth_context.subject)
    status_payload["transport"] = "http"
    status_body = json.dumps(status_payload, indent=2, sort_keys=True).encode("utf-8")
    health_body = json.dumps(build_health_payload(), indent=2, sort_keys=True).encode("utf-8")

    class BootstrapHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                body = status_body
            elif self.path == "/healthz":
                body = health_body
            else:
                self.send_response(404)
                self.end_headers()
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/echo":
                self.send_response(404)
                self.end_headers()
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)

            try:
                parsed_body = json.loads(raw_body.decode("utf-8")) if raw_body else None
            except json.JSONDecodeError:
                parsed_body = raw_body.decode("utf-8")

            response_body = json.dumps(
                {
                    "status": "ok",
                    "method": "POST",
                    "path": self.path,
                    "received": parsed_body,
                },
                indent=2,
                sort_keys=True,
            ).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((settings.host, settings.port), BootstrapHandler)
    print(f"Serving HTTP endpoint on http://{settings.host}:{settings.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
