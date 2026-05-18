## HISTORICAL LOG ANALYSIS (last 5 days from DB)

{historical_data}

---
## YOUR TASK: TEMPORAL COMPARISON

You have two sources of data:
1. **Historical summaries** (above) — LLM analyses stored in DB from the last 5 days.
2. **Current logs** (below) — raw Docker logs covering up to 5 days, analysed fresh.

Focus your analysis on the **last 24h** of events (most recent log lines), but use the
historical summaries to identify **trends and changes**:

- If an attack pattern appeared in history but is absent in the last 24h → report as
  **calmed down / resolved** and note when it last appeared.
- If a new error class appears today that was NOT in history → flag as **new issue**.
- If a recurring problem persists across multiple days → note it is **ongoing / persistent**.
- If all health metrics improved vs. yesterday → note positive trend.

Always anchor your **severity** to the last 24h state, not the full 5-day window.

If no historical data is available (first run), analyse logs as usual and set
`trend_summary` to `"No prior data available for comparison."`
