## SECURITY ANALYSIS — OWASP EXPERTISE

Use this skill after suspicious traffic or security-relevant findings have
already been identified. Focus on security interpretation, OWASP mapping,
severity escalation, and attack-stage classification rather than raw pattern
detection mechanics.

You are also a security auditor. Apply the latest OWASP standards when analyzing logs:
- OWASP Top 10 (2021 edition — the most recent published version as of 2026)
- OWASP API Security Top 10 (2023 edition — relevant for HTTP API backends)

**OWASP Top 10 — log-observable indicators**:
- A01 Broken Access Control: repeated 403s, path traversal (`../`), unauthorized admin access
- A02 Cryptographic Failures: HTTP (non-HTTPS) requests to sensitive endpoints
- A03 Injection: SQL/command/LDAP patterns in URLs (`' OR 1=1`, `; DROP`, `%27`,
  `<script>`, `${jndi:`)
- A05 Security Misconfiguration: probing for `/phpmyadmin`, `/actuator`, `/.git/`, `/.env`, `/debug`
- A07 Auth/Identification Failures: credential stuffing — many login 401s or
  authentication lockouts clustering in short windows
- A09 Security Logging Failures: gaps in log timestamps (potential log tampering)

**OWASP API Security Top 10 — additional API-specific indicators**:
- API1 Broken Object Level Authorization: requests crafting IDs to access other users' resources
- API3 Broken Object Property Level: requests with unexpected fields in payloads
- API4 Unrestricted Resource Consumption: bulk requests or large payloads hitting `/api/` rapidly
- API8 Security Misconfiguration: verbs not in use returning unexpected 2xx (e.g. DELETE, TRACE)

**Attack lifecycle stages**:
- Reconnaissance: probing many different paths quickly (automated scanner fingerprint)
- Enumeration: repeated hits on similar paths with small variations
- Exploitation: 200/302 responses on paths that should never succeed
- Impact: large response sizes (data exfiltration), privilege escalation signs

**Severity escalation rules (CVSS-style)**:
- Reconnaissance only (all 404s) → WARNING
- Sensitive file probe returned 200 → CRITICAL immediately
- Admin brute-force or repeated authentication lockouts → CRITICAL if >3 lockouts in a day
- Injection attempt strings in URLs → CRITICAL regardless of response code
- Scanner hitting >20 unique non-existent paths → CRITICAL

**Reverse-proxy log interpretation**:
- Proxy timestamps usually represent real client request time and are often
  more reliable for traffic timing than application logs
- High volume from single IP with varied User-Agents = bot rotation
- Request gaps <100ms = automated scanner, not human
- Response size 0 bytes on 403 = proxy blocked before the application (good — block worked)
