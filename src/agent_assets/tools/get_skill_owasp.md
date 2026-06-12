# Action: `read_skills` with `skill_names=["owasp_security"]`

Purpose:
- return the OWASP-focused monitoring skill for security-oriented log analysis

When to use:
- when logs suggest probing, scanning, injection attempts, auth abuse, or suspicious access patterns
- when you need security classification guidance before finalizing the report

When not to use:
- when the findings are clearly routine and no security interpretation is needed
- when the OWASP skill was already retrieved in the current loop and no new evidence justifies repeating it

Arguments:
- `skill_names`: include `owasp_security`

Output shape:
- `action`
- `skills`
