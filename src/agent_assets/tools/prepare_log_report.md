# Tool Selection: Final Report Readiness

Purpose:
- describe when the LLM should stop requesting tools and return `final_report`

Use `final_report` when:
- deterministic tool results explain application errors, proxy status distribution, and security noise
- high 4xx ratios have denominators and a scanner-vs-application-path interpretation
- zero-line and unavailable sources are recorded as coverage gaps
- optional security skills have been fetched when suspicious evidence requires them

Do not use a fake `prepare_log_report` tool.
Return the `final_report` action directly when evidence is sufficient.

Final report output shape is defined by `monitoring_log_response_format.md`.
