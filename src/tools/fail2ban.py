"""Post-MVP MCP tool for strictly allowlisted fail2ban live-status diagnostics.

TODO(post-MVP): verify this tool on the VPS after MCP can run `fail2ban-client`
with the host fail2ban socket mounted. Phase 6 MVP should use collected
`vps-security` fail2ban log sources and snapshot tools instead.
"""

from __future__ import annotations

import logging

from fastmcp.tools.base import ToolResult

from auth.scopes import MCP_STATUS_READ_SCOPE
from decorators import project_authorized_tool, workflow_discoverable_tool
from logging_config import get_logger
from services.fail2ban_service import Fail2banActivity, Fail2banService
from tools.agent_hints import INSPECT_LIVE_FAIL2BAN_ACTIVITY_TOOL_DESCRIPTION
from tools.models import (
    Fail2banJailStatusPayload,
    Fail2banServiceStatusPayload,
    InspectLiveFail2banActivityPayload,
)

logger: logging.Logger = get_logger("tools.fail2ban")
fail2ban_service = Fail2banService()


def _build_fail2ban_payload(
    *,
    project_name: str,
    activity: Fail2banActivity,
) -> InspectLiveFail2banActivityPayload:
    """Convert service-layer fail2ban status into the MCP response contract."""

    return InspectLiveFail2banActivityPayload(
        action="inspect_live_fail2ban_activity",
        project_name=project_name,
        inspection_status=activity.inspection_status,
        error_code=activity.error_code,
        message=activity.message,
        retry_tips=activity.retry_tips,
        service=Fail2banServiceStatusPayload(
            inspection_status=activity.service.inspection_status,
            jail_count=activity.service.jail_count,
            jails=activity.service.jails,
            error=activity.service.error,
        ),
        jails=[
            Fail2banJailStatusPayload(
                jail=jail.jail,
                inspection_status=jail.inspection_status,
                currently_failed=jail.currently_failed,
                total_failed=jail.total_failed,
                currently_banned=jail.currently_banned,
                total_banned=jail.total_banned,
                banned_ips=jail.banned_ips,
                error=jail.error,
            )
            for jail in activity.jails
        ],
    )


@workflow_discoverable_tool(
    MCP_STATUS_READ_SCOPE,
    mcp_description=INSPECT_LIVE_FAIL2BAN_ACTIVITY_TOOL_DESCRIPTION,
)
@project_authorized_tool
async def inspect_live_fail2ban_activity(
    project_name: str | None = None,
) -> ToolResult:
    """TODO(post-MVP): inspect live fail2ban runtime state.

    This tool is intentionally separate from `collect_logs`. `collect_logs`
    reads manifest-backed files such as `/var/log/fail2ban.log` into a snapshot
    so agents can grep, group, and build incident bundles over historical
    evidence. This live tool asks the running fail2ban daemon for its current
    service and jail state through allowlisted `fail2ban-client status`
    commands.

    Expected use after VPS validation:
    - `project_name="vps-security"`
    - answer "which jails exist right now?"
    - answer "which IPs are currently banned?"
    - report unavailable/error state when the MCP image lacks
      `fail2ban-client` or cannot access the mounted host fail2ban socket

    It must not accept caller-provided commands or replace historical
    fail2ban log analysis.
    """

    assert project_name is not None
    activity = fail2ban_service.inspect_activity()
    payload = _build_fail2ban_payload(project_name=project_name, activity=activity)
    logger.info(
        "tool result",
        extra={
            "event": "tool_result",
            "tool_name": "inspect_live_fail2ban_activity",
            "project_name": payload.project_name,
            "inspection_status": payload.inspection_status,
            "jail_count": len(payload.jails),
        },
    )
    return ToolResult(content=[], structured_content=payload.model_dump(mode="json"))
