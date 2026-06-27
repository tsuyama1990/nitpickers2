# AUDITOR FEEDBACK

{{feedback}}

## Response Guidelines

### 🔴 FATAL issues (marked with `[FATAL]`):
You **MUST** fix these. The Auditor will reject the code if these are not addressed.
Examples: Hardcoding, Security vulnerabilities, Spec violations.

### 🟡 WARNING issues (marked with `[WARNING]`):
These are **advisory**. Use your judgment as a senior engineer:
- If you have a valid architectural or practical reason to skip, you may do so
- If the suggestion improves code quality without breaking scope, implement it
- You may acknowledge and defer low-priority warnings to a future cycle

**Your model is more capable than the Auditor's. If you disagree with a finding, state your reasoning clearly in the commit message.**

{{#pr_url}}
Previous PR: {{pr_url}}
{{/pr_url}}
