# Monitoring Tool Loop User Prompt

You must respond with JSON only.

Choose one action:
- `call_tools`
- `read_skills`
- `final_report`

Use `call_tools` when you still need deterministic data or an optional workflow
tool result.
Use `read_skills` when deterministic evidence shows that optional bot-detection
or OWASP/security guidance is needed before the final report.
If tool results show bot, scanner, probe, credential, sensitive-path, or
suspicious 4xx traffic and `bot_detection` is available but not yet read, choose
`read_skills` for `bot_detection` before `final_report`.
If tool results show possible security impact, successful sensitive-path access,
auth/admin/API abuse, injection or path-traversal patterns, malicious-input 5xx,
security-control failure, or unclear impact on real application/admin/API
routes, and `owasp_security` is available but not yet read, choose `read_skills`
for `owasp_security` before `final_report`.
Use `final_report` when you have enough information to finish the job.

You may request one or more tools in a single step.

Required top-level response shapes:

For tool calls:
```json
{
  "action": "call_tools",
  "tool_calls": [
    {
      "tool_name": "inspect_proxy_activity",
      "arguments": {
        "project_name": "landingpage",
        "source_keys": ["nginx", "traefik"]
      }
    }
  ]
}
```

For final report:
```json
{
  "action": "final_report",
  "summary": "Short overall summary"
}
```

For optional skill reads:
```json
{
  "action": "read_skills",
  "skill_names": ["owasp_security"]
}
```

When `action` is `final_report`, use the exact field contract from
`monitoring_log_response_format.md`.

Do not return:
```json
{
  "final_report": {
    "summary": "..."
  }
}
```
