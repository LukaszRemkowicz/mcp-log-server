Return JSON only — no explanatory text outside the JSON.

Canonical final log-report shape:

{
  "action": "final_report",
  "summary": "Brief overview of the day's log health (2-3 sentences)",
  "severity": "INFO|WARNING|CRITICAL",
  "severity_rationale": "One sentence explaining why the severity is INFO, WARNING, or CRITICAL",
  "key_findings": ["specific finding 1", "specific finding 2"],
  "evidence": ["tool-backed fact 1", "tool-backed fact 2"],
  "coverage_gaps": ["source or check that was unavailable, empty, or inconclusive"],
  "recommendations": "Concrete next steps referencing this project's code and services",
  "watch_only_items": ["normal recurring noise that does not need immediate action"],
  "trend_summary": "1-2 sentences on what changed vs. prior days (e.g. attack calmed down)"
}

Rules:
- `key_findings` is the canonical findings field for the final report
- do not invent alternate top-level field names
- `severity_rationale` must explain the severity using deterministic evidence
  and private monitoring context
- `evidence` must cite deterministic tool results or collection facts, not model guesses
- `coverage_gaps` must name unavailable, zero-line, or uninspected sources when relevant
- for zero-line sources, say the source was not assessed from logs; do not say
  "no errors found", "no warnings found", or "healthy"
- HTTP/proxy findings should include denominator counts and percentages when
  deterministic tool results provide them
- describe suspicious traffic by evidence-backed families or behavior, not by
  unproven application technology
- use retrieved optional skills for detailed bot, security, severity, and
  recommendation wording
- `watch_only_items` is where normal recurring noise belongs
- `findings` may be included only as a backward-compatible alias when needed by
  the runtime, but prefer `key_findings`
