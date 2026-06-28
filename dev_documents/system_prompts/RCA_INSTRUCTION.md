# SYSTEM POST-MORTEM REQUEST
Cycle ID: {cycle_id}
Trace ID: {trace_id}
Error: {error}

## FAILURE SNAPSHOT (GIT DIFF & STATE)
```json
{sanitized_state}
```

## LOCAL LOG TAIL (LAST 100 LINES)
```text
{sanitized_log}
```

---
# INSTRUCTIONS
Analyze the data above and provide a concise (max 300 words) Root Cause Analysis.
1. Identify the likely technical cause (e.g. timeout, state mismatch, git conflict).
2. Cross-reference the Log Tail with the Git Diff if possible.
3. Suggest a concrete fix or next step for the human operator.
4. If a LangSmith Trace ID is present, remind the user to check it for detailed LLM thoughts.

Format: Markdown.
