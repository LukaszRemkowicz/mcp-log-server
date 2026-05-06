"""Centralized agent-facing hints for MCP log-analysis tools.

This module keeps the LLM guidance layer separate from the deterministic tool
implementations. The tools still own their real behavior, but the wording that
teaches agents how to chain those tools together lives in one place so it can
evolve without cluttering business logic.
"""

COLLECT_LOGS_TOOL_DESCRIPTION = (
    "Collect deterministic logs for one or more projects into persisted artifacts "
    "for later analysis. "
    'Use workspace="workflow" only for the fixed shared monitoring flow. '
    'Use workspace="session" when an agent wants its own investigation workspace. '
    "session_id is optional in the schema but required for session collections, "
    "and the same session_id can be reused to collect additional projects into "
    "the same investigation. "
    "Use source_keys to limit collection to selected sources. "
    "Use since/until to collect a narrower incident window. "
    "After session collection, use session_id plus project_name with follow-up tools. "
    "After workflow collection, use project_name for the newest workflow artifact, "
    "or add archive_name when you need one archived workflow artifact."
)

BUILD_INCIDENT_BUNDLE_TOOL_DESCRIPTION = (
    "Build one compact incident summary from a persisted snapshot. "
    "Use it as an entry point, not as a substitute for raw log context. "
    "Before drawing final conclusions about timing, clustering, or severity, "
    "recollect a tighter since/until window or reopen the relevant snapshot files "
    "and grep results."
)

CREATE_FILTERED_VIEW_TOOL_DESCRIPTION = (
    "Create a cleaned deterministic view from a persisted raw snapshot. "
    "This keeps the raw snapshot as the source of truth while removing "
    "low-signal lines through manifest-selected noise profiles. "
    "Use it when you want a smaller analysis view before reading or grepping "
    "raw files directly."
)

GROUP_ERRORS_TOOL_DESCRIPTION = (
    "Group repeated error-like lines from one persisted workflow or session "
    "snapshot into compact triage findings. Use it after collect_logs when you "
    "need recurring failures, timestamps, source keys, and raw line references "
    "before deciding whether to grep, read files, or recollect a narrower "
    "since/until window."
)

SUGGEST_FOLLOWUP_WINDOW_TOOL_DESCRIPTION = (
    "Convert first_timestamp and last_timestamp from group_errors or "
    "build_incident_bundle into a tighter collect_logs since/until window. "
    "Use this after grouped analysis when an incident span is too broad and "
    "the next step should be recollecting a narrower workflow or session "
    "snapshot. This tool does not read logs and does not need project_name or "
    "session_id; pass the returned since/until values into collect_logs."
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
    (
        "For workflow investigations, use project_name for the newest workflow artifact, "
        "or add archive_name for one archived workflow artifact."
    ),
    "Call list_log_snapshot_files to inspect which persisted source files are available.",
    "Call grep_log_snapshot or group_errors before opening large files in full.",
]

LIST_SNAPSHOT_NEXT_STEP_TIPS = [
    (
        "Choose one source_key from this inventory before calling "
        "read_log_snapshot_file or grep_log_snapshot."
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
    "Use match_offset and match_limit to page through additional grep results.",
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
        "Reuse the same session_id if you want to replace the current session "
        "snapshot with this narrower window."
    ),
]

FILTERED_VIEW_NEXT_STEP_TIPS = [
    "Use the cleaned_lines first for a smaller incident-oriented view of the snapshot.",
    (
        "Reopen raw context with read_log_snapshot_file or grep_log_snapshot if an excluded "
        "line still matters for the investigation."
    ),
]
