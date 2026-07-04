"""Dispatch decoded socket requests to fixed Docker operations."""

from __future__ import annotations

from typing import Any

from .exceptions import ProtocolException
from .schemas import SuccessResponse
from .services import SocketOperationRegistry


def dispatch_request(
    request: dict[str, Any],
    operation_registry: SocketOperationRegistry,
) -> SuccessResponse:
    """Validate a decoded request object and run the requested operation.

    This function works on already-decoded JSON. It checks the common envelope
    shape, then delegates operation-specific parameter validation to the
    operation registry.
    """

    operation = request.get("operation")
    if not isinstance(operation, str) or not operation:
        raise ProtocolException("Request field 'operation' must be a non-empty string.")

    params = request.get("params", {})
    if not isinstance(params, dict):
        raise ProtocolException("Request field 'params' must be an object.")

    return {"ok": True, "result": operation_registry.dispatch(operation, params)}
