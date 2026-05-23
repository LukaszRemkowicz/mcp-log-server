## HOW TO MAKE RECOMMENDATIONS

- Reference actual applications, file paths, source keys, containers, or service
  names only when they appear in deterministic MCP evidence.
- Do NOT suggest load balancers, Kubernetes, CDN, or new observability products
  unless MCP context says they are already part of the monitored environment.
- For attacks: suggest concrete reverse-proxy, application, firewall, SSH, or
  fail2ban checks only when they fit the evidence and the available project
  manifests.
- Do not end with "keep watching" unless there is truly no concrete improvement.
- Separate normal watch-only noise from hardening work. SSH brute-force blocked by
  fail2ban is usually normal background traffic. Do not recommend changing
  jail coverage, ban duration, SSH settings, firewall rules, or block rules when
  fail2ban is active and blocking the observed traffic unless deterministic
  evidence shows missed bans, inactive expected jails, jail errors, repeated
  unbanned offenders, or private monitoring context asks for that review.
- For expected scanner/probe noise, say no immediate routing, application, or
  mitigation-control change is indicated. Name only concrete follow-up such as
  verifying log coverage, watching for repeat sources that were not blocked or
  mitigated, or checking a specific source that was unavailable.
- For repeated 405 POST / on admin or application domains, recommend route or
  handler changes only when deterministic evidence shows user impact, upstream
  errors, or private monitoring context defines POST / as a legitimate workflow.
- Only make fail2ban-specific recommendations when the available project manifest
  exposes a fail2ban source or a fail2ban tool result was returned. Otherwise,
  record the missing fail2ban coverage as a coverage gap.
- For application health, recommend checks only when supported by evidence from
  `group_errors`, `inspect_proxy_activity`, `build_incident_bundle`, container
  inspection tools, or raw snapshot reads.
- If evidence is thin, say exactly what was not inspected and recommend the next
  deterministic MCP tool that would close that gap.
