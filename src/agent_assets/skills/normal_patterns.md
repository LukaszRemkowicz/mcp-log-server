## KNOWN NORMAL LOG PATTERNS — do NOT flag as issues

- `GET /health` or `/ping/` — health probes from reverse proxies or orchestrators
- Authentication lockout entries after repeated failed admin logins — expected
  security behavior unless volume or impact escalates
- Background scheduler or worker heartbeat messages — normal operation
- `Replacing N existing analysis record(s)` — idempotent log analysis, not an error
- HTTP 304 Not Modified on static files — correct caching
