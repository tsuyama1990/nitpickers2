from typing import Any

from rich.console import Console

from src.config import settings
from src.enums import FlowStatus, WorkPhase
from src.services.base_jules_usecase import BaseJulesUseCase
from src.state import CycleState
from src.utils import logger

console = Console()


class CriticUseCase(BaseJulesUseCase):
    """Handles the self-critic and final-critic phases."""

    async def execute(self, state: CycleState, is_final: bool = False) -> dict[str, Any]:
        cycle_id = state.cycle_id
        session_id = state.session.jules_session_name

        if not session_id:
            logger.error(f"[CRITIC] Failed: No session ID for cycle {cycle_id}")
            return {"status": FlowStatus.FAILED, "error": "No session ID for critic phase"}

        if is_final:
            console.print(
                "[bold cyan]Invoking Final Coder Critic for self-reflection before completion...[/bold cyan]"
            )
            template_file = settings.template_files.final_coder_critic_instruction
        else:
            console.print(
                "[bold cyan]Invoking Coder Critic for self-reflection before Auditor review...[/bold cyan]"
            )
            template_file = settings.template_files.coder_critic_instruction

        critic_instruction = settings.get_prompt_content(template_file)
        if not critic_instruction:
            logger.warning("Coder Critic template missing, skipping...")
            return {"status": FlowStatus.COMPLETED}

        critic_instruction = critic_instruction.replace("{{cycle_id}}", str(cycle_id))
        console.print("[dim]Waiting for Coder Critic to finish review and push fixes...[/dim]")
        result = await self.jules.continue_session(session_id, critic_instruction)

        if result and (result.get("status") == "success" or result.get("pr_url")):
            await self._update_last_processed_commit(state, result.get("branch_name"))

            pr_url = result.get("pr_url") or state.session.pr_url
            branch_name = result.get("branch_name") or state.session.branch_name

            session_update = state.session.model_copy(
                update={
                    "pr_url": pr_url,
                    "branch_name": branch_name,
                }
            )

            phase_type = "final self critic review" if is_final else "selfcritic review"
            if pr_url:
                console.print(f"[bold green]PR Point ({phase_type}):[/bold green] {pr_url}")

            return {
                "status": FlowStatus.COMPLETED,
                "session": session_update,
                "branch_name": branch_name,
                "pr_url": pr_url,
                "current_phase": WorkPhase.FINAL_CRITIC if is_final else WorkPhase.SELF_CRITIC,
            }

        return {"status": FlowStatus.FAILED, "error": "Critic phase failed"}
