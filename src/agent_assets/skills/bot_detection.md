## BOT / ATTACK DETECTION — treat as CRITICAL

Use this skill to detect suspicious traffic patterns in the logs and describe
what happened. Focus on scanner behavior, repeated probing, clustering, and
timestamp extraction rather than OWASP categorization.

Logs include per-line timestamps (ISO 8601 format, added by `docker compose logs --timestamps`).
When you detect scanning or probing, always extract and report the timestamp of the LAST
suspicious request from the log line.

**Attack indicators**:
- Probing for sensitive files: `/.env`, `/.git/config`, `/wp-admin/`, `/phpMyAdmin/`,
  `/config.php`, `/.htaccess`, `/backup`, `/shell`, `/api/v1/`, `/v1/image/`
- Rapid repeated 404s on non-existent paths (>5 in a short time window)
- Repeated 403 Forbidden on `/admin/` with no Referer (CSRF probe)
- `Method Not Allowed` on `/` — likely non-browser client probing
- `Not Acceptable` responses on `/` — content-type probing bots

**Probe vector families**:
- Secret/config disclosure probes: `/.env`, `/.env.local`, `/config.php`,
  `/.htaccess`, `/settings.py`, `/application.yml`
- Source repository exposure probes: `/.git/config`, `/.svn/`, `/composer.json`,
  `/package.json`
- Debug/info disclosure probes: `/phpinfo.php`, `/debug`, `/actuator/env`,
  `/server-status`
- Backup/database dump discovery: `/backup.sql`, `/dump.sql`, `/database.sql`,
  `/backup.zip`, `/backup.tar.gz`
- CMS/PHP ecosystem probes: `/wp-login.php`, `/xmlrpc.php`, `/wp-admin/`,
  `/wp-content/plugins/...`, `/phpMyAdmin/`, `/pma`
- Shell/upload exploit probes: `/shell`, `/upload.php`, `/vendor/phpunit/...`,
  `/eval-stdin.php`
- Misleading infrastructure-warning probes: some scanners hit paths that make
  reverse proxies, routers, or middleware emit scary-looking infrastructure
  warnings even though the underlying service is healthy. Do not classify these
  warnings from the message alone. Compare the suspicious token/path shape,
  nearby proxy/app logs, response status, and service-health evidence.
  Treat the warning as very likely scanner noise only when the evidence lines up:
  the token or path looks like a commodity probe (`index.php`, `file.php`,
  `wp-config.php`, `.env`, `/wp-admin/`, backup filenames); nearby requests show
  the same probe family or source pattern; responses are blocked or
  missing-resource statuses such as 403/404/405; service/container health is
  normal; and there is no independent evidence of router failure, certificate
  renewal failure, successful access, or service impact. ACME challenge warnings
  with PHP/CMS-looking token names are one example of this pattern, not a standalone rule.

**Noise-vs-incident reasoning checklist**:
- Do not call something noise from one warning line. Build the conclusion from
  multiple deterministic facts.
- Strong scanner-noise evidence: probe-shaped token/path, clustered PHP/CMS or
  sensitive-file probes in the same time window, blocked/missing-resource
  responses, current runtime security-daemon state when available, healthy
  containers/services, and no follow-through error showing the real platform
  feature failed. Zero currently banned IPs only means no IPs are banned at
  inspection time; do not treat it as evidence of past mitigation or successful
  protection unless another tool result explicitly proves that history or
  effectiveness.
- Weak or inconclusive evidence: warning text without nearby request context,
  zero-line sources, missing proxy logs, unknown service health, or no check of
  the affected subsystem.
- Use uncertainty in the report. Prefer "very likely scanner noise" or "appears
  consistent with scanner noise" instead of "proven harmless".
- If proof is required, recommend a deterministic follow-up check for the
  affected subsystem, for example certificate expiry/ACME state for certificate
  warnings, router health for routing warnings, or current served response for
  suspicious paths.

When describing probe families, preserve uncertainty. Prefer wording like
"appears consistent with a WordPress/CMS probe vector" or "likely commodity
CMS/PHP scanner traffic" instead of stating that the monitored app is running
WordPress/PHP or that the attacker's intent is proven. Do not imply the
monitored app runs WordPress, PHP, Laravel, Joomla, or another stack unless
project context or private monitoring context says so.

**When you detect an attack pattern, your finding MUST include**:
  1. What was probed (e.g. '.env, .git/config')
  2. How many requests
  3. The timestamp of the LAST probe from the log line (format: HH:MM:SS UTC)
