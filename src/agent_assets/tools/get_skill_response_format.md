# Tool: `get_skill_response_format`

Purpose:
- return the required final response format for log-analysis output

When to use:
- when you need the exact final response structure before producing the final report
- when output-shape precision matters more than free-form explanation

When not to use:
- when you already know the final response contract for the current job
- when the tool has already been called in the current loop without any schema change

Output shape:
- `skill_name`
- `content`
