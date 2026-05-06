import asyncio
import time
from typing import Any

from rich.console import Console

from src.config import settings
from src.enums import FlowStatus, WorkPhase
from src.state import CycleState

console = Console()


class CommitteeUseCase:
    """
    Encapsulates the logic for managing the Committee of Auditors.
    """

    async def execute(self, state: CycleState) -> dict[str, Any]:  # noqa: PLR0911
        """Node for Managing the Committee of Auditors."""
        if state.status == FlowStatus.WAITING_FOR_JULES:
            console.print(
                "[bold yellow]No new commit detected. Waiting for Jules to complete work...[/bold yellow]"
            )
            return {
                "status": FlowStatus.WAIT_FOR_JULES_COMPLETION,
            }

        audit_res = state.audit_result
        i: int = state.current_auditor_index
        j: int = state.current_auditor_review_count
        current_iter: int = state.iteration_count

        if audit_res and audit_res.is_approved:
            if i < settings.NUM_AUDITORS:
                next_idx = i + 1
                console.print(
                    f"[bold green]Auditor #{i} Approved. Moving to Auditor #{next_idx}.[/bold green]"
                )
                committee_update = state.committee.model_copy(
                    update={
                        "current_auditor_index": next_idx,
                        "current_auditor_review_count": 1,
                    }
                )
                return {
                    "committee": committee_update,
                    "status": FlowStatus.NEXT_AUDITOR,
                }
            console.print(
                "[bold green]All Auditors Approved! Transitioning to Final Refactoring...[/bold green]"
            )
            return {"status": FlowStatus.COMPLETED, "current_phase": WorkPhase.REFACTORING}

        if j < settings.REVIEWS_PER_AUDITOR:
            next_rev = j + 1
            console.print(
                f"[bold yellow]Auditor #{i} Rejected. "
                f"Retry {next_rev}/{settings.REVIEWS_PER_AUDITOR}.[/bold yellow]"
            )
            last_fb = state.get("last_feedback_time", 0)
            now = time.time()
            cooldown = 180
            elapsed = now - last_fb

            if elapsed < cooldown and last_fb > 0:
                wait = cooldown - elapsed
                console.print(
                    f"[bold yellow]Cooldown: Waiting {int(wait)}s before next Coder session...[/bold yellow]"
                )
                await asyncio.sleep(wait)

            committee_update = state.committee.model_copy(
                update={
                    "current_auditor_review_count": next_rev,
                    "iteration_count": current_iter + 1,
                }
            )
            return {
                "committee": committee_update,
                "status": FlowStatus.RETRY_FIX,
                "last_feedback_time": time.time(),
            }

        if i < settings.NUM_AUDITORS:
            next_idx = i + 1
            console.print(
                f"[bold yellow]Auditor #{i} limit reached. "
                f"Fixing code then moving to Auditor #{next_idx}.[/bold yellow]"
            )
            last_fb = state.get("last_feedback_time", 0)
            now = time.time()
            cooldown = 180
            elapsed = now - last_fb

            if elapsed < cooldown and last_fb > 0:
                wait = cooldown - elapsed
                console.print(
                    f"[bold yellow]Cooldown: Waiting {int(wait)}s before next Coder session...[/bold yellow]"
                )
                await asyncio.sleep(wait)

            committee_update = state.committee.model_copy(
                update={
                    "current_auditor_index": next_idx,
                    "current_auditor_review_count": 1,
                    "iteration_count": current_iter + 1,
                }
            )
            return {
                "committee": committee_update,
                "status": FlowStatus.RETRY_FIX,
                "last_feedback_time": time.time(),
            }

        # --- Final Fallback: Budget Exhausted ---
        # If we reach here and final_fix is ALREADY True, it means we've already done the polish.
        # We must transition to the next phase (final_critic or completed).
        if state.current_phase == WorkPhase.FINAL_CRITIC:
            console.print("[bold green]Final Polish complete. Moving to Final Review.[/bold green]")
            return {"status": FlowStatus.COMPLETED}

        round_count = (
            state.current_auditor_index - 1
        ) * settings.REVIEWS_PER_AUDITOR + state.current_auditor_review_count
        console.print(
            f"[bold red]Final Auditor budget reached ({round_count} rounds). Fixing code one last time then Merging.[/bold red]"
        )

        committee_update = state.committee.model_copy(
            update={
                "iteration_count": current_iter + 1,
            }
        )
        return {
            "current_phase": WorkPhase.FINAL_CRITIC,
            "committee": committee_update,
            "status": FlowStatus.RETRY_FIX,
            "last_feedback_time": time.time(),
        }
