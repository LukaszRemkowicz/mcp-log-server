Return JSON only — no explanatory text outside the JSON.

Canonical final log-report shape:

{
  "action": "final_report",
  "summary": "Brief overview of the day's log health (2-3 sentences)",
  "severity": "INFO|WARNING|CRITICAL",
  "key_findings": ["specific finding 1", "specific finding 2"],
  "recommendations": "Concrete next steps referencing this project's code and services",
  "trend_summary": "1-2 sentences on what changed vs. prior days (e.g. attack calmed down)"
}

Rules:
- `key_findings` is the canonical findings field for the final report
- do not invent alternate top-level field names
- `findings` may be included only as a backward-compatible alias when needed by
  the runtime, but prefer `key_findings`
