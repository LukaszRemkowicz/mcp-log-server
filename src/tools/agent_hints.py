"""Centralized agent-facing hints for MCP log-analysis tools.

This module keeps the LLM guidance layer separate from the deterministic tool
implementations. The tools still own their real behavior, but the wording that
teaches agents how to chain those tools together lives in one place so it can
evolve without cluttering business logic.
"""

COLLECT_LOGS_TOOL_DESCRIPTION = (
    "Collect deterministic logs into a persisted snapshot for later analysis. "
    'Use workspace="workflow" only for the fixed shared monitoring flow. '
    'Use workspace="session" with a unique session_id when an agent wants its '
    "own investigation snapshot that can be replaced by later recollection. "
    "Use source_keys to limit collection to selected sources. "
    "Use since/until to collect a narrower incident window. "
    "Use the returned snapshot_id with snapshot follow-up tools such as "
    "list_log_snapshot_files, read_log_snapshot_file, and grep_log_snapshot."
)

BUILD_INCIDENT_BUNDLE_TOOL_DESCRIPTION = (
    "Build one compact incident summary from a persisted snapshot. "
    "Use it as an entry point, not as a substitute for raw log context. "
    "Before drawing final conclusions about timing, clustering, or severity, "
    "recollect a tighter since/until window or reopen the relevant snapshot files "
    "and grep results."
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
    "Use the returned snapshot_id for all follow-up snapshot tools.",
    "Call list_log_snapshot_files to inspect which persisted source files are available.",
    "Call grep_log_snapshot or group_errors before opening large files in full.",
]

LIST_SNAPSHOT_NEXT_STEP_TIPS = [
    (
        "Choose one source_key from this inventory before calling "
        "read_log_snapshot_file or grep_log_snapshot."
    ),
    'Use snapshot_id="latest" only when you intentionally want the newest workflow snapshot.',
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
