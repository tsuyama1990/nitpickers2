from typing import Any

from rich.console import Console

from src.enums import FlowStatus, WorkPhase
from src.services.base_jules_usecase import BaseJulesUseCase
from src.state import CycleState
from src.state_manager import StateManager
from src.utils import logger

console = Console()


class FeedbackFixUseCase(BaseJulesUseCase):
    """Handles auditor feedback fixes and post-audit refactoring."""

    async def execute(self, state: CycleState) -> dict[str, Any]:
        cycle_id = state.cycle_id
        mgr = StateManager()
        cycle_manifest = mgr.get_cycle(cycle_id)

        is_refactor = state.current_phase == WorkPhase.REFACTORING
        phase_label = "Refactor" if is_refactor else "Feedback Fix"

        session_id = state.session.jules_session_name or (
            cycle_manifest.jules_session_id if cycle_manifest else None
        )

        if not session_id:
            logger.error(f"[{phase_label}] Failed: No session ID for cycle {cycle_id}")
            return {"status": FlowStatus.FAILED, "error": f"No session ID for {phase_label}"}

        instruction = self._build_instruction(cycle_id, state.current_phase, state, cycle_manifest)

        console.print(f"[bold green]Applying {phase_label} to session {session_id}...[/bold green]")
        result = await self.jules.continue_session(session_id, instruction)

        if result and (result.get("status") == "success" or result.get("pr_url")):
            # --- VERIFY COMMIT PROGRESS ---
            branch_val = result.get("branch_name") or (
                cycle_manifest.branch_name if cycle_manifest else None
            )
            new_commit = (
                await self.jules.get_latest_branch_commit(branch_val) if branch_val else None
            )

            if new_commit and new_commit == state.last_processed_commit:
                logger.error(
                    f"[{phase_label}] Failed: Jules reported success but no new commits found on {branch_val}."
                )
                return {
                    "status": FlowStatus.FAILED,
                    "error": f"Jules failed to push changes for {phase_label} (Commit stuck at {new_commit})",
                }

            await self._update_last_processed_commit(state, branch_val)

            pr_val = result.get("pr_url") or (cycle_manifest.pr_url if cycle_manifest else None)

            # --- SMART PHASE TRANSITION ---
            if is_refactor:
                target_status = FlowStatus.READY_FOR_FINAL_CRITIC
                next_phase = WorkPhase.REFACTORING
                checkpoint_label = "(refactoring/polish)"
            elif state.status == FlowStatus.TDD_FAILED:
                # STAY in coder phase to ensure self-critic is triggered after pass
                target_status = FlowStatus.READY_FOR_SELF_CRITIC
                next_phase = WorkPhase.CODER
                checkpoint_label = "(verification fix)"
            else:
                target_status = FlowStatus.READY_FOR_AUDIT
                next_phase = WorkPhase.AUDIT
                checkpoint_label = "(audit feedback response)"

            mgr.update_cycle_state(
                cycle_id, pr_url=pr_val, branch_name=branch_val, status=target_status
            )

            if pr_val:
                console.print(f"[bold green]PR Point {checkpoint_label}:[/bold green] {pr_val}")

            session_update = state.session.model_copy(
                update={"pr_url": pr_val, "branch_name": branch_val}
            )

            return {
                "status": target_status,
                "session": session_update,
                "branch_name": branch_val,
                "pr_url": pr_val,
                "current_phase": next_phase,
            }

        return {"status": FlowStatus.FAILED, "error": f"{phase_label} session failed"}
