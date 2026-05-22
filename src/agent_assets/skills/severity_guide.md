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
