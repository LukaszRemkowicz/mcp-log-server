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
from services.traefik_tls_service import (
    TraefikRouterTlsInspection,
    TraefikTlsInspectionError,
    TraefikTlsInspectionResult,
    TraefikTlsService,
)
from tools.agent_hints import (
    INSPECT_TLS_CERTIFICATE_TOOL_DESCRIPTION,
    INSPECT_TRAEFIK_TLS_CONFIGURATION_TOOL_DESCRIPTION,
)
from tools.models import (
    InspectTlsCertificatePayload,
    InspectTraefikTlsConfigurationPayload,
    TlsCertificateInspectionPayload,
    TraefikRouterTlsPayload,
)

logger: logging.Logger = get_logger("tools.tls")
tls_certificate_service = TlsCertificateService()
traefik_tls_service = TraefikTlsService()


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


def _build_traefik_router_tls_payload(
    result: TraefikTlsInspectionResult | TraefikTlsInspectionError,
) -> InspectTraefikTlsConfigurationPayload:
    """Convert service-layer Traefik TLS facts into the MCP response contract."""

    if isinstance(result, TraefikTlsInspectionError):
        return InspectTraefikTlsConfigurationPayload(
            action="inspect_traefik_tls_configuration",
            inspection_status="unavailable",
            router_count=0,
            truncated=False,
            routers=[],
            warnings=[result.message],
        )

    router_payloads = [_build_traefik_router_payload(router) for router in result.routers]
    return InspectTraefikTlsConfigurationPayload(
        action="inspect_traefik_tls_configuration",
        inspection_status="ok",
        router_count=len(router_payloads),
        truncated=result.truncated,
        routers=router_payloads,
        warnings=[],
    )


def _build_traefik_router_payload(
    router: TraefikRouterTlsInspection,
) -> TraefikRouterTlsPayload:
    """Convert one service-layer Traefik router row into its MCP payload."""

    return TraefikRouterTlsPayload(
        router_name=router.router_name,
        container_name=router.container_name,
        rule=router.rule,
        entrypoints=router.entrypoints,
        service=router.service,
        tls_enabled=router.tls_enabled,
        cert_resolver=router.cert_resolver,
        certificate_source=router.certificate_source,
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


@workflow_discoverable_tool(
    MCP_STATUS_READ_SCOPE,
    mcp_description=INSPECT_TRAEFIK_TLS_CONFIGURATION_TOOL_DESCRIPTION,
)
async def inspect_traefik_tls_configuration() -> ToolResult:
    """Inspect sanitized Traefik router TLS runtime configuration."""

    result = traefik_tls_service.inspect_router_tls()
    payload = _build_traefik_router_tls_payload(result)
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "inspect_traefik_tls_configuration",
            "inspection_status": payload.inspection_status,
            "router_count": payload.router_count,
            "truncated": payload.truncated,
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))
