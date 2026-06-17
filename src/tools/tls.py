"""MCP tool for bounded TLS certificate diagnostics."""

from __future__ import annotations

import logging

from fastmcp.tools.base import ToolResult

from auth.scopes import MCP_STATUS_READ_SCOPE
from conf import settings
from decorators import workflow_discoverable_tool
from logging_config import get_logger
from services.tls_certificate_service import (
    TlsCertificateInspection,
    TlsCertificateService,
    TlsInspectionStatus,
)
from tools.agent_hints import INSPECT_TLS_CERTIFICATE_TOOL_DESCRIPTION
from tools.models import InspectTlsCertificatePayload, TlsCertificateInspectionPayload

logger: logging.Logger = get_logger("tools.tls")
tls_certificate_service = TlsCertificateService()


def _build_tls_certificate_payload(
    inspections: list[TlsCertificateInspection],
) -> InspectTlsCertificatePayload:
    """Convert service-layer TLS facts into the MCP response contract."""

    inspection_payloads = [
        TlsCertificateInspectionPayload(
            domain_key=inspection.domain_key,
            hostname=inspection.hostname,
            port=inspection.port,
            inspection_status=inspection.inspection_status,
            warning_level=inspection.warning_level,
            subject_summary=inspection.subject_summary,
            issuer_summary=inspection.issuer_summary,
            not_before=inspection.not_before,
            not_after=inspection.not_after,
            days_until_expiry=inspection.days_until_expiry,
            hostname_matches=inspection.hostname_matches,
            matched_names=inspection.matched_names,
            error_code=inspection.error_code,
            message=inspection.message,
        )
        for inspection in inspections
    ]
    if any(inspection.inspection_status == "unavailable" for inspection in inspection_payloads):
        inspection_status: TlsInspectionStatus = "unavailable"
    elif any(inspection.inspection_status == "warning" for inspection in inspection_payloads):
        inspection_status = "warning"
    else:
        inspection_status = "ok"
    return InspectTlsCertificatePayload(
        action="inspect_tls_certificate",
        site_domain=settings.SITE_DOMAIN.strip() or None,
        configured_subdomains=[str(item) for item in settings.TLS_CERTIFICATE_SUBDOMAINS],
        inspection_status=inspection_status,
        inspections=inspection_payloads,
    )


@workflow_discoverable_tool(
    MCP_STATUS_READ_SCOPE,
    mcp_description=INSPECT_TLS_CERTIFICATE_TOOL_DESCRIPTION,
)
async def inspect_tls_certificate() -> ToolResult:
    """Inspect TLS certificate expiry for SITE_DOMAIN and configured subdomains."""

    inspections = tls_certificate_service.inspect_certificates()
    payload = _build_tls_certificate_payload(inspections)
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "inspect_tls_certificate",
            "hostnames": [inspection.hostname for inspection in payload.inspections],
            "inspection_status": payload.inspection_status,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))
