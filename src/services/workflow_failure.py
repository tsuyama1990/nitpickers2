"""Diagnostic snapshot and state serialization for pipeline failures.

Split from workflow.py — part of WorkflowService decomposition.
"""

import json
from datetime import UTC
from typing import Any, cast

from rich.console import Console

from src.services.git_ops import GitManager
from src.utils import logger

console = Console()


class WorkflowFailureHandler:
    """Failure snapshot creation and state serialization helpers.

    Mixin class — depends on self being a WorkflowService instance
    that provides self.git, self._background_tasks.
    """

    def _serialize_state_data(self, state: "Any") -> dict[str, Any]:  # CycleState | dict
        """Helper to serialize state into a dict."""
        import json as _json

        def pydantic_encoder(obj: Any) -> Any:
            if hasattr(obj, "model_dump"):
                return obj.model_dump(mode="json")
            if hasattr(obj, "dict"):
                return obj.dict()
            if hasattr(obj, "value"):  # Enum
                return obj.value
            msg = f"Object of type {type(obj).__name__} is not JSON serializable"
            raise TypeError(msg)

        try:
            if hasattr(state, "model_dump"):
                return state.model_dump(mode="json")  # type: ignore[no-any-return]

            # Use json round-trip with fallback encoder
            return cast(dict[str, Any], _json.loads(_json.dumps(state, default=pydantic_encoder)))
        except Exception as e:
            logger.warning(f"Failed to fully serialize state: {e}")
            # Last-resort: stringify each value individually
            state_data: dict[str, Any] = {}
            if isinstance(state, dict):
                for k, v in state.items():
                    try:
                        state_data[k] = _json.loads(_json.dumps(v, default=pydantic_encoder))
                    except Exception:
                        state_data[k] = str(v)
            else:
                state_data = {"error": "Serialization failed", "raw": str(state)}
            return state_data

    def _get_llm_optimized_state(self, state: "Any") -> dict[str, Any]:  # CycleState | dict
        """Truncates the state to prevent RCA context overflow."""
        state_data = self._serialize_state_data(state)

        # Truncate messages to last 10 turns
        session = state_data.get("session")
        if session and isinstance(session, dict):
            msgs = session.get("messages")
            if isinstance(msgs, list) and len(msgs) > 10:
                session["messages"] = msgs[-10:]
                session["_truncated"] = True

        return state_data

    async def _save_failure_snapshot(
        self,
        cycle_id: str,
        state: "Any",  # CycleState | dict
        error_msg: str,
        git_manager: "GitManager | None" = None,
    ) -> None:
        """Saves a diagnostic snapshot of the system state upon failure."""
        import asyncio as _asyncio
        from datetime import datetime as _datetime
        from pathlib import Path as _Path

        from src.services.rca_service import RCAService
        from src.utils import current_trace_id as _current_trace_id

        # Prepare failure snapshot directory
        cycles_dir = _Path("logs/cycles")
        await _asyncio.to_thread(cycles_dir.mkdir, parents=True, exist_ok=True)

        snapshot_file = cycles_dir / f"failure_{cycle_id}.json"

        # Prepare minimal state for LLM to avoid context limits
        optimized_state = self._get_llm_optimized_state(state)

        # 2. Truncated Filesystem Snapshot (Git Diff)
        git = git_manager or GitManager()
        try:
            raw_status = await git.get_status()
        except Exception as e:
            raw_status = f"Failed to capture diff: {e}"

        # Limit diff to 1000 lines
        lines = raw_status.splitlines()
        if len(lines) > 1000:
            diff = "\n".join(lines[:1000]) + "\n... [TRUNCATED - 1000 line limit]"
        else:
            diff = raw_status

        # Assemble full diagnostic payload
        diagnostic_data = {
            "timestamp": _datetime.now(UTC).isoformat(),
            "cycle_id": cycle_id,
            "trace_id": _current_trace_id.get(),
            "error": error_msg,
            "git_diff": diff,
            "state": optimized_state,
        }

        # Save snapshot
        await _asyncio.to_thread(snapshot_file.write_text, json.dumps(diagnostic_data, indent=2))

        # Trigger RCAService (also fire-and-forget)
        rca = RCAService()
        # We don't await this here, just initiate it
        task = _asyncio.create_task(rca.analyze_failure(cycle_id, snapshot_file))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        console.print(
            "[bold magenta]AI Post-Mortem Analysis triggered in background.[/bold magenta]"
        )
