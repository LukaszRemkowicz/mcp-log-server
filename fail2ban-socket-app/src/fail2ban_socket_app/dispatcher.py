"""Dispatch decoded socket requests to fixed fail2ban diagnostics."""

from __future__ import annotations

from typing import Any

from .exceptions import ProtocolException
from .schemas import SuccessResponse
from .services import Fail2banSocketService


def dispatch_request(
    request: dict[str, Any],
    service: Fail2banSocketService,
) -> SuccessResponse:
    """Validate a decoded request object and run the requested operation."""

    operation = request.get("operation")
    if not isinstance(operation, str) or not operation:
        raise ProtocolException("Request field 'operation' must be a non-empty string.")

    params = request.get("params", {})
    if not isinstance(params, dict):
        raise ProtocolException("Request field 'params' must be an object.")

    return {"ok": True, "result": service.dispatch(operation, params)}
