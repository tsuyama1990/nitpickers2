import uuid
from datetime import UTC, datetime
from typing import Any

from rich.console import Console

from src.config import settings
from src.enums import FlowStatus, WorkPhase
from src.services.base_jules_usecase import BaseJulesUseCase
from src.services.git_ops import workspace_lock
from src.state import CycleState
from src.state_manager import StateManager
from src.utils import logger

console = Console()


class InitialCoderUseCase(BaseJulesUseCase):
    """Handles the initial coding phase where the first PR is generated."""

    async def execute(self, state: CycleState) -> dict[str, Any]:
        cycle_id = state.cycle_id
        mgr = StateManager()
        cycle_manifest = mgr.get_cycle(cycle_id)

        if not cycle_manifest:
            msg = f"Cycle manifest not found for cycle {cycle_id}"
            raise ValueError(msg)

        # Check for existing session (Resume logic)
        logger.info(f"Checking resume for cycle {cycle_id}")
        jules_session_name: str | None = None
        result = None

        if cycle_manifest.jules_session_id and cycle_manifest.jules_session_id != "null":
            session_state = await self.jules.get_session_state(cycle_manifest.jules_session_id)
            if session_state == "COMPLETED":
                jules_session_name = cycle_manifest.jules_session_id
                console.print(
                    f"[bold blue]Session {jules_session_name} is COMPLETED. Retrieving result...[/bold blue]"
                )
                result = await self.jules.wait_for_completion(
                    jules_session_name, expect_new_work=False
                )
            elif session_state == "RUNNING":
                jules_session_name = cycle_manifest.jules_session_id
                console.print(
                    f"[bold blue]Session {jules_session_name} is RUNNING. Waiting...[/bold blue]"
                )
                result = await self.jules.wait_for_completion(
                    jules_session_name, expect_new_work=False
                )

        # Launch NEW session if no existing result
        if not result:
            console.print(
                f"[bold green]Starting Initial Coder Session for Cycle {cycle_id}...[/bold green]"
            )
            async with workspace_lock:
                target_branch = cycle_manifest.branch_name if cycle_manifest.branch_name else None
                instruction = self._build_instruction(
                    cycle_id, WorkPhase.CODER, state, cycle_manifest
                )
                target_files = settings.get_target_files()
                context_files = settings.get_context_files()

                timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
                session_req_id = f"coder-cycle-{cycle_id}-{timestamp}-{uuid.uuid4().hex[:6]}"

                jules_session_name, result = await self._run_jules_session(
                    session_req_id,
                    instruction,
                    target_files,
                    context_files,
                    cycle_id,
                    mgr,
                    branch=target_branch,
                )

                if result.get("status") == "running" and jules_session_name:
                    result = await self.jules.wait_for_completion(jules_session_name)

        if result and (result.get("status") == "success" or result.get("pr_url")):
            branch_val = result.get("branch_name") or cycle_manifest.branch_name
            new_commit = (
                await self.jules.get_latest_branch_commit(branch_val) if branch_val else None
            )

            if not new_commit or new_commit == "unknown":
                logger.error(f"Initial Coder Failed: No commit found on {branch_val}")
                return {"status": FlowStatus.FAILED, "error": "Jules failed to push initial commit"}

            await self._update_last_processed_commit(state, branch_val)

            pr_val = result.get("pr_url") or cycle_manifest.pr_url

            mgr.update_cycle_state(
                cycle_id,
                pr_url=pr_val,
                branch_name=branch_val,
                status=FlowStatus.READY_FOR_SELF_CRITIC,
            )

            if pr_val:
                console.print(f"[bold green]PR Point (coder instruction):[/bold green] {pr_val}")

            session_name_str: str = jules_session_name or ""
            session_update = state.session.model_copy(
                update={
                    "jules_session_name": session_name_str,
                    "pr_url": pr_val,
                    "branch_name": branch_val,
                }
            )

            return {
                "status": FlowStatus.READY_FOR_SELF_CRITIC,
                "session": session_update,
                "branch_name": branch_val,
                "pr_url": pr_val,
                "current_phase": WorkPhase.CODER,
            }

        return {"status": FlowStatus.FAILED, "error": "Initial coder session failed"}
