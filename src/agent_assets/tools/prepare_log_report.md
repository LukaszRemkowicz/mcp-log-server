# Tool: `prepare_log_report`

Purpose:
- return the prepared log-monitoring payload for the current scheduled job

When to use:
- when you need the current log findings before writing the final analysis
- when the job has log scope and no prepared log report has been retrieved yet

When not to use:
- when the current job is not a log-monitoring job
- when the same prepared log report has already been returned and no new context exists

Output shape:
- structured log report payload for the current job
- includes severity, summary, findings, recommendations, and trend data when available
- may include deterministic `probe_blocking_context` data describing suspicious
  probe IPs, fail2ban policy thresholds, and observed ban activity
