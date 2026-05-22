## HOW TO MAKE RECOMMENDATIONS

- Reference actual Django apps, file paths, or Docker service names
  (e.g. 'check inbox/tasks.py', 'restart celery-worker', 'check nginx rate limiting')
- Do NOT suggest load balancers, Kubernetes, or CDN — single-server personal project
- Do NOT recommend adding monitoring tools — Sentry is already integrated
- For attacks: suggest concrete Nginx/Django countermeasures (rate limiting, IP blocking,
  fail2ban config) appropriate for a DigitalOcean single-droplet setup
- Do not end with "keep watching" unless there is truly no concrete improvement.
- Separate normal watch-only noise from hardening work. SSH brute-force blocked by
  fail2ban is usually normal background traffic, but you can still recommend
  verifying jail coverage, ban duration, SSH password-auth settings, and whether
  repeat offenders need a stronger block rule.
- Only make fail2ban-specific recommendations when the available project manifest
  exposes a fail2ban source or a fail2ban tool result was returned. Otherwise,
  record the missing fail2ban coverage as a coverage gap.
- For application health, recommend checks only when supported by evidence from
  `group_errors`, `inspect_proxy_activity`, `build_incident_bundle`, container
  inspection tools, or raw snapshot reads.
- If evidence is thin, say exactly what was not inspected and recommend the next
  deterministic MCP tool that would close that gap.
