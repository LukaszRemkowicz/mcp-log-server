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
