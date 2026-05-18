## WHAT TO LOOK FOR

- 5xx errors from Gunicorn or Django
- Traefik router failures, entrypoint errors, or ACME/certificate renewal issues
- PostgreSQL connection errors or slow queries (>1s)
- Redis connection failures
- Celery task failures or retries (especially `monitoring.tasks` or `inbox.tasks`)
- Repeated 401/403 on API endpoints beyond normal axes behavior
- Gunicorn worker timeouts or crashes
- Email delivery failures in `common.services` or `inbox.tasks`
- OpenAI API errors (rate limits, timeouts) in `translation` or `monitoring` apps
- Unusual traffic spikes or crawler abuse on `/api/` endpoints
