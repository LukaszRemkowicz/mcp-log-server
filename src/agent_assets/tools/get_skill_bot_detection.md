# Tool: `get_skill_bot_detection`

Purpose:
- return the bot-detection monitoring skill for suspicious traffic analysis

When to use:
- when logs show repeated probes, suspicious 4xx clusters, or clear scanner behavior
- when you need timestamp and attack-pattern extraction guidance

When not to use:
- when the findings are ordinary application errors with no suspicious traffic pattern
- when the skill was already retrieved in the current loop and no new traffic evidence exists

Output shape:
- `skill_name`
- `content`
