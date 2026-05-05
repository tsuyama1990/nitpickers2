import json
from pathlib import Path
from typing import Any

from rich.console import Console

from src.utils import logger

console = Console()


class RCAService:
    """
    Synthesizes failure logs and state snapshots to provide an AI-driven Root Cause Analysis.
    """

    def __init__(self, model: str | None = None) -> None:
        from src.config import settings

        self.model = model or settings.agents.rca_model

    async def analyze_failure(self, cycle_id: str, snapshot_path: Path) -> str:
        """Performs a synthesized analysis of a cycle failure."""
        import asyncio

        if not await asyncio.to_thread(snapshot_path.exists):
            return f"Error: Snapshot not found at {snapshot_path}"

        try:
            snapshot_data = json.loads(await asyncio.to_thread(snapshot_path.read_text))
            log_tail = self._get_log_tail(cycle_id)

            analysis = await self._call_rca_llm(cycle_id, snapshot_data, log_tail)

            # Save the analysis alongside the snapshot
            report_path = snapshot_path.with_suffix(".rca.md")
            await asyncio.to_thread(report_path.write_text, analysis)
        except Exception as e:
            logger.exception("RCA analysis failed")
            return f"RCA failed: {e}"
        else:
            return analysis

    def _get_log_tail(self, cycle_id: str, lines: int = 100) -> str:
        """Retrieves the last N lines of the cycle log."""
        log_path = Path(f"logs/cycles/cycle_{cycle_id}.log")
        if not log_path.exists():
            return "No log found."

        try:
            with log_path.open() as f:
                content = f.readlines()
                return "".join(content[-lines:])
        except Exception as e:
            return f"Failed to read logs: {e}"

    async def _call_rca_llm(self, cycle_id: str, snapshot: dict[str, Any], log_tail: str) -> str:
        """Calls the LLM to synthesize the failure data."""
        import litellm

        from src.utils_sanitization import sanitize_for_llm

        # Sanitize sensitive data before sending to diagnostic LLM
        sanitized_state = sanitize_for_llm(json.dumps(snapshot, indent=2))
        sanitized_log = sanitize_for_llm(log_tail)

        prompt = f"""
# SYSTEM POST-MORTEM REQUEST
Cycle ID: {cycle_id}
Trace ID: {snapshot.get("trace_id", "N/A")}
Error: {snapshot.get("error", "Unknown")}

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
"""
        try:
            response = await litellm.acompletion(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a Root Cause Analysis (RCA) expert for an AI agentic system.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
            )
            return str(response.choices[0].message.content)
        except Exception as e:
            return f"LLM analysis failed: {e}"
