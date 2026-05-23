## SEVERITY CLASSIFICATION

- **INFO**: Normal operation. Routine requests, scheduled tasks completed, no issues.
  Automated SSH brute-force attempts that are detected and blocked by fail2ban
  are INFO unless they coincide with service impact, sensitive data exposure, or
  failed mitigation.
- **WARNING**: Degraded but operational. A few 4xx errors, one failed Celery retry,
  slow DB query, reconnaissance-only attack (all 404s).
- **CRITICAL**: Service-affecting OR active exploitation attempt. 5xx errors,
  DB/Redis unreachable, Celery tasks exhausted retries, email delivery failed,
  sensitive file returned 200, injection strings in URLs.

HTTP/proxy severity rules:

- Judge 4xx/405/404 traffic by ratio and path context, not by count alone.
- A small 4xx ratio on scanner-looking paths is usually INFO/watch-only.
- A high 4xx ratio on real application or API paths is WARNING until proven
  expected by private monitoring context or deterministic evidence.
- Do not call a high 4xx ratio normal operation unless path distribution or
  private monitoring context proves the traffic is expected scanner noise.
- If the ratio is high and expected-noise context is unclear, prefer WARNING or
  explicitly state the uncertainty instead of INFO.
- Treat 4xx ratios at or above 20% as high enough to require explanation.
- Treat 4xx ratios at or above 50% as suspicious unless the paths are clearly
  scanner-only or expected noise.
- Do not summarize high 4xx ratios on admin, API, or application paths as
  "normal operation"; classify them as WARNING unless deterministic evidence or
  private monitoring context proves they are expected.
- If high 4xx traffic is dominated by scanner-only paths, blocked probes, or
  disallowed methods with no 5xx, no upstream errors, no successful abuse, and
  no private-context expectation that the route is legitimate, classify it as
  watch-only security noise instead of an application defect.
- Repeated 405 POST / on an admin or application domain is likely bot/probe
  traffic when private monitoring context does not define POST / as a legitimate
  workflow; do not raise severity for routing or handler misconfiguration unless
  tool evidence shows user impact, upstream errors, or a real expected client
  using that route.
- Any 5xx/upstream failure affecting real application paths is at least WARNING
  and may be CRITICAL when repeated or service-affecting.
- Zero-line sources are coverage gaps, not health evidence.
