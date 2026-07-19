from __future__ import annotations

import json
import logging

from socket_app.logging_config import JsonFormatter


def test_json_formatter_emits_socket_request_fields() -> None:
    record = logging.LogRecord(
        name="socket_app.server",
        level=logging.ERROR,
        pathname=__file__,
        lineno=12,
        msg="socket_request_completed",
        args=(),
        exc_info=None,
    )
    record.operation = "service_health"
    record.ok = True
    record.duration_ms = 1.25
    record.error_category = "docker_backend"
    record.error_code = "docker_backend_error"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "socket_request_completed"
    assert payload["level"] == "ERROR"
    assert payload["logger"] == "socket_app.server"
    assert payload["operation"] == "service_health"
    assert payload["ok"] is True
    assert payload["duration_ms"] == 1.25
    assert payload["error_category"] == "docker_backend"
    assert payload["error_code"] == "docker_backend_error"
    assert payload["timestamp"].endswith("+00:00")
