"""Centralized agent-facing hints for MCP log-analysis tools.

This module keeps the LLM guidance layer separate from the deterministic tool
implementations. The tools still own their real behavior, but the wording that
teaches agents how to chain those tools together lives in one place so it can
evolve without cluttering business logic.
"""

COLLECT_LOGS_TOOL_DESCRIPTION = (
    "Collect deterministic logs for one or more projects into persisted artifacts "
    "for later analysis. "
    "MCP chooses the artifact workspace from the authenticated caller. "
    "For a new session collection, omit session_id and MCP will create one. "
    "For follow-up collection in the same investigation, reuse the returned "
    "session_id to collect additional projects or narrower windows. "
    "Use source_keys to limit collection to selected sources. "
    "Use since/until to collect a narrower incident window. "
    "After session collection, use session_id plus project_name with follow-up tools. "
    "After workflow collection, use project_name for the newest workflow artifact, "
    "or add archive_name when you need one archived workflow artifact."
)

CLOSE_AGENT_SESSION_TOOL_DESCRIPTION = (
    "Close one interactive investigation session after the agent is done with "
    "session-scoped collection and follow-up reads. This marks the session as "
    "closed in audit metadata without deleting snapshot files. Use the exact "
    "session_id returned by collect_logs. The fixed workflow-agent cannot close "
    "interactive sessions."
)

BUILD_INCIDENT_BUNDLE_TOOL_DESCRIPTION = (
    "Build one compact incident summary from a persisted snapshot. "
    "Use it as an entry point, not as a substitute for raw log context. "
    "Use source_key for one source or source_keys for multiple sources; do not pass both. "
    "Before drawing final conclusions about timing, clustering, or severity, "
    "recollect a tighter since/until window or reopen the relevant snapshot files "
    "and grep results."
)

CREATE_FILTERED_VIEW_TOOL_DESCRIPTION = (
    "Create a cleaned deterministic view from a persisted raw snapshot. "
    "This keeps the raw snapshot as the source of truth while removing "
    "low-signal lines through manifest-selected noise profiles. "
    "Use source_key for one source or source_keys for multiple sources; do not pass both. "
    "Use view_mode='head' for chronological cleaned lines, view_mode='errors' "
    "for incident-oriented lines first, or view_mode='sample' for a broader "
    "spread across selected sources. "
    "Use it when you want a smaller analysis view before reading or grepping "
    "raw files directly."
)

GROUP_ERRORS_TOOL_DESCRIPTION = (
    "Group repeated error-like lines from one persisted workflow or session "
    "snapshot into compact triage findings. Use it after collect_logs when you "
    "need recurring failures, timestamps, source keys, and raw line references. "
    "Use source_key for one source or source_keys for multiple sources, but not both, "
    "before deciding whether to grep, read files, or recollect a narrower "
    "since/until window."
)

INSPECT_PROXY_ACTIVITY_TOOL_DESCRIPTION = (
    "Inspect collected proxy-shaped snapshot sources for deterministic ingress "
    "signals. Summarizes HTTP status classes, route/status clusters, and "
    "upstream-style 502/503/504 errors from persisted logs. This tool reads "
    "snapshot files only; it does not run live proxy or shell commands."
)

INSPECT_PROBE_BLOCKING_ACTIVITY_TOOL_DESCRIPTION = (
    "Inspect collected fail2ban plus nginx/Traefik access snapshot sources for "
    "deterministic probe-blocking correlation. Returns sensitive-path probe "
    "requests by IP, whether each IP should have crossed the configured retry "
    "threshold, and whether historical fail2ban Ban or already-banned events "
    "were observed. This tool reads snapshot files only and does not run live "
    "fail2ban commands."
)

INSPECT_LIVE_FAIL2BAN_ACTIVITY_TOOL_DESCRIPTION = (
    "Inspect live fail2ban runtime state for a project such as vps-security "
    "through a fixed allowlist of fail2ban-client status commands. Returns "
    "active jail names, per-jail ban counters, and currently banned IPs when "
    "the fail2ban client and host socket are available to MCP. This tool does "
    "not run caller-provided shell commands and does not collect historical "
    "logs; use collect_logs plus the fail2ban source for historical incident "
    "analysis."
)

INSPECT_CONTAINERS_HEALTH_TOOL_DESCRIPTION = (
    "Inspect Docker runtime status for all docker-backed sources in one project. "
    "Returns a compact per-source overview with container status, healthcheck "
    "status when available, restart count, image, and lifecycle timestamps "
    "without exposing raw docker ps output."
)

INSPECT_VPS_CONTAINERS_TOOL_DESCRIPTION = (
    "Inspect all Docker containers visible to the MCP runtime, like a bounded "
    "read-only docker ps view for VPS incident triage. Returns container id, "
    "name, image, command preview, created/status/state fields, published ports, "
    "safe Compose labels, restart policy, network names, health status, restart "
    "count, and deterministic triage notes without exposing raw inspect JSON, "
    "environment values, host mount source paths, or mutation operations."
)

INSPECT_CONTAINER_DETAIL_TOOL_DESCRIPTION = (
    "Inspect curated Docker metadata for one manifest-approved source container. "
    "Use this after inspect_containers_health points to a suspicious container. "
    "Returns bounded docker-inspect-style details such as status, image, restart "
    "policy, ports, command, entrypoint, working directory, runtime user, env var "
    "names without values, label keys without values, selected safe Compose label "
    "values, mounts without host source paths, networks and aliases, and recent "
    "healthcheck log entries."
)

GREP_LOG_SNAPSHOT_TOOL_DESCRIPTION = (
    "Search one persisted workflow or session snapshot with controlled grep "
    "semantics. Use grep for an extended regex pattern, for example "
    "'Ban|wp-login|502'. Omit source filters to search all saved files, pass "
    "source_key for one source, or pass source_keys for multiple sources; do "
    "not pass both."
)

SUGGEST_FOLLOWUP_WINDOW_TOOL_DESCRIPTION = (
    "Convert first_timestamp and last_timestamp from group_errors or "
    "build_incident_bundle into a tighter collect_logs since/until window. "
    "Use this after grouped analysis when an incident span is too broad and "
    "the next step should be recollecting a narrower workflow or session "
    "snapshot. This tool does not read logs and does not need project_name or "
    "session_id; pass the returned since/until values into collect_logs."
)

READ_CONTAINER_FILE_TOOL_DESCRIPTION = (
    "Read text content from one explicit file path inside a manifest-approved "
    "docker source container. Use list_container_directory first to navigate "
    "from the source's main project folder. The path argument is required and "
    "must be an absolute path inside the selected container. "
    "This tool rejects directories and bounds the returned content with max_bytes."
)

STAT_CONTAINER_PATH_TOOL_DESCRIPTION = (
    "Return metadata for one explicit file or directory path inside a "
    "manifest-approved docker source container without reading file contents. "
    "Use this to check whether a path exists, whether it is a directory, its "
    "size, mode, and modified timestamp before deciding whether to read or list it."
)

LIST_CONTAINER_DIRECTORY_TOOL_DESCRIPTION = (
    "List files and directories inside a manifest-approved docker source "
    "container, like running ls -la in a terminal. If path is omitted or blank, "
    "the tool lists the source's first approved inspection root, usually the "
    "main project folder such as /app/. Directory paths return immediate "
    "children; file paths return one metadata entry for that file."
)

LOG_ANALYSIS_CAUTIONS = [
    "Use grouped findings for triage, not as the final incident conclusion.",
    (
        "Confirm timing, clustering, and severity with grep_log_snapshot(...) "
        "or read_log_snapshot_file(...)."
    ),
    (
        "Use the original collection window to judge whether a pattern is "
        "bursty, continuous, or isolated."
    ),
]

COLLECT_LOGS_NEXT_STEP_TIPS = [
    "For session investigations, use session_id plus project_name for later follow-up tools.",
    "Call close_agent_session with the session_id when the interactive investigation is done.",
    (
        "For workflow investigations, use project_name for the newest workflow artifact, "
        "or add archive_name for one archived workflow artifact."
    ),
    "Call list_log_snapshot_files to inspect which persisted source files are available.",
    "Call grep_log_snapshot or group_errors before opening large files in full.",
]

LIST_SNAPSHOT_NEXT_STEP_TIPS = [
    (
        "Choose one source_key for read_log_snapshot_file, or pass it as "
        "source_key/source_keys to grep and analysis tools."
    ),
    "Omit archive_name when you intentionally want the newest workflow artifact.",
]

READ_SNAPSHOT_NEXT_STEP_TIPS = [
    "Use start_line and line_count to reopen a smaller chunk when the file is large.",
    (
        "Use grep_log_snapshot first if you need to locate a narrower pattern "
        "before reading more context."
    ),
]

GREP_SNAPSHOT_NEXT_STEP_TIPS = [
    "Use match_offset and max_matches to page through additional grep results.",
    (
        "Reopen the matching file with read_log_snapshot_file around the "
        "reported line numbers for more context."
    ),
]

GROUP_ERRORS_NEXT_STEP_TIPS = [
    (
        "Use first_timestamp and last_timestamp to decide whether the issue "
        "looks bursty, continuous, or isolated."
    ),
    (
        "Call suggest_followup_window with the grouped timestamps, then "
        "recollect with collect_logs using the returned since/until values."
    ),
]

PROXY_ACTIVITY_NEXT_STEP_TIPS = [
    "Use top_routes to decide which host/path/status cluster needs raw context.",
    (
        "Reopen raw lines with read_log_snapshot_file before concluding whether "
        "traffic failed at the edge proxy or upstream application."
    ),
    (
        "If 502, 503, or 504 errors are clustered, inspect the related app "
        "container logs for the same time window."
    ),
]

INCIDENT_BUNDLE_NEXT_STEP_TIPS = [
    (
        "Start from the highest-severity top_groups entry, then reopen the "
        "raw snapshot context before drawing final conclusions."
    ),
    (
        "If the bundle spans too much time, derive a tighter recollection "
        "window with suggest_followup_window and collect_logs again."
    ),
]

FOLLOWUP_WINDOW_NEXT_STEP_TIPS = [
    "Use the returned since and until values in a new collect_logs call.",
    (
        "Reuse the returned session_id if you want to replace the current session "
        "snapshot with this narrower window, or omit it to start a new session."
    ),
]

FILTERED_VIEW_NEXT_STEP_TIPS = [
    "Use the cleaned_lines first for a smaller incident-oriented view of the snapshot.",
    (
        "Reopen raw context with read_log_snapshot_file or grep_log_snapshot if an excluded "
        "line still matters for the investigation."
    ),
]
