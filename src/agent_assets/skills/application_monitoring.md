## WHAT TO LOOK FOR

- 5xx errors from application servers, backend frameworks, or reverse proxies
- Traefik router failures, entrypoint errors, or ACME/certificate renewal issues
- PostgreSQL connection errors or slow queries (>1s)
- Redis connection failures
- Background worker task failures or retries
- Repeated 401/403 on API endpoints beyond known normal authentication behavior
- Application worker timeouts or crashes
- Email delivery failures
- External API errors such as rate limits, timeouts, or provider failures
- Unusual traffic spikes or crawler abuse on `/api/` endpoints
