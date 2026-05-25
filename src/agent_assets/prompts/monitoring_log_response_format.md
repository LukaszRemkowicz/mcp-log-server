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
  and private monitoring context, especially when high 4xx or attack traffic is
  classified as INFO/watch-only
- `evidence` must cite deterministic tool results or collection facts, not model guesses
- `coverage_gaps` must name unavailable, zero-line, or uninspected sources when relevant
- for zero-line sources, say the source was not assessed from logs; do not say
  "no errors found", "no warnings found", or "healthy"
- HTTP/proxy findings must include denominator counts and percentages when
  judging whether 4xx/405/404 traffic is normal, suspicious, or broken
- do not write "normal operation" for high 4xx ratios on admin, API, or
  application paths unless deterministic evidence or private monitoring context
  proves the traffic is expected
- if high 4xx traffic is scanner-only or blocked-probe noise with no 5xx,
  no upstream errors, and no private-context expectation that the route is
  legitimate, put it in `watch_only_items` and avoid recommending routing,
  application, or mitigation-control changes
- repeated 405 POST / on an admin or application domain is likely bot/probe
  traffic unless private monitoring context defines POST / as a legitimate
  workflow or tool evidence shows user impact
- when scanner paths appear, describe them as likely probe families rather than
  proven app technology: config/secret disclosure, repository exposure,
  debug/info disclosure, backup/database dump discovery, CMS/PHP probes, or
  shell/upload exploit probes. Do not imply the monitored app runs WordPress,
  PHP, Laravel, Joomla, or another stack unless project context says so
- do not recommend fail2ban jail, ban-duration, or firewall changes when
  fail2ban is active and blocking the observed traffic unless evidence shows
  missed bans, inactive expected jails, jail errors, or repeated unbanned
  offenders
- `watch_only_items` is where normal blocked bot or SSH brute-force noise belongs
- `findings` may be included only as a backward-compatible alias when needed by
  the runtime, but prefer `key_findings`
