# Action: `read_skills`

Purpose:
- return one or more optional workflow skills as deterministic context for the next LLM turn

When to use:
- when the current deterministic findings need optional guidance before the final report
- when suspicious traffic needs `bot_detection`
- when security interpretation needs `owasp_security`

When not to use:
- for mandatory baseline skills already injected into the system prompt
- to fetch project-private context, which belongs to the monitoring app
- repeatedly with the same `skill_name` unless new evidence justifies it

Arguments:
- `skill_names`: optional skill names from `analyze_daily_log_bundle.optional_skills`

Output shape:
- `action`
- `skills`
