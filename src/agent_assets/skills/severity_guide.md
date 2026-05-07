## SEVERITY CLASSIFICATION

- **INFO**: Normal operation. Routine requests, scheduled tasks completed, no issues.
- **WARNING**: Degraded but operational. A few 4xx errors, one failed Celery retry,
  slow DB query, reconnaissance-only attack (all 404s).
- **CRITICAL**: Service-affecting OR active exploitation attempt. 5xx errors,
  DB/Redis unreachable, Celery tasks exhausted retries, email delivery failed,
  sensitive file returned 200, injection strings in URLs.
