# Monitoring Tool Loop User Prompt

You must respond with JSON only.

Choose one action:
- `call_tools`
- `final_report`

Use `call_tools` when you still need data or a project skill fragment.
Use `final_report` when you have enough information to finish the job.

You may request one or more tools in a single step.

Required top-level response shapes:

For tool calls:
```json
{
  "action": "call_tools",
  "tool_calls": [
    {
      "tool_name": "prepare_log_report",
      "arguments": {}
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
