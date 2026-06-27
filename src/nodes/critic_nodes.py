"""Consolidated critic/auditor node implementations.

Merged from: architect_critic.py, auditor.py, coder_critic.py
"""

from typing import Any

from rich.console import Console

from src.enums import FlowStatus, WorkPhase
from src.state import CycleState

console = Console()


# ---------------------------------------------------------------------------
#  ArchitectCriticNodes
# ---------------------------------------------------------------------------

class ArchitectCriticNodes:
    def __init__(self, jules_client: Any, git_manager: Any | None = None) -> None:
        self.jules = jules_client
        from src.services.self_critic_evaluator import SelfCriticEvaluator

        self.evaluator = SelfCriticEvaluator(jules_client)
        from src.services.git_ops import GitManager

        self.git = git_manager or GitManager()

    async def architect_critic_node(self, state: CycleState) -> dict[str, Any]:
        """Node for running the Architect Self-Critic evaluation."""
        console.print("[bold blue]Starting Architect Critic Node...[/bold blue]")

        session_id = state.project_session_id
        if not session_id:
            return {
                "status": FlowStatus.ARCHITECT_FAILED,
                "error": "No session ID found for Critic Evaluation",
            }

        critic_result, pr_url, _branch_name = await self.evaluator.evaluate(session_id)

        pr_url = pr_url or state.session.pr_url
        if pr_url:
            console.print(f"[bold green]PR Point [Architect Self-Critic]:[/bold green] {pr_url}")

        critic_retry_count = state.critic_retry_count

        if critic_result.is_approved or critic_retry_count >= 0:
            if critic_result.is_approved:
                console.print("[bold green]Architecture Approved by Critic![/bold green]")
            else:
                console.print(
                    "[bold yellow]Max Architect Critic retries reached. Forcing approval.[/bold yellow]"
                )

            pr_url = state.session.pr_url
            if pr_url:
                pr_number = pr_url.split("/")[-1]
                try:
                    console.print(f"[bold blue]Merging Architecture PR #{pr_number}...[/bold blue]")
                    await self.git.merge_pr(pr_number)
                    console.print("[bold green]Architecture merged successfully![/bold green]")
                except Exception as e:
                    console.print(f"[bold red]Failed to merge Architecture PR: {e}[/bold red]")

            return {"status": FlowStatus.ARCHITECT_COMPLETED}

        critic_retry_count += 1
        console.print(
            f"[bold yellow]Architecture Rejected by Critic (Retry {critic_retry_count}/1)[/bold yellow]"
        )

        feedback_prompt = (
            "The architecture was rejected. Please fix the following vulnerabilities:\n"
        )
        for vuln in critic_result.vulnerabilities:
            feedback_prompt += f"- {vuln}\n"
        if critic_result.suggestions:
            for sugg in critic_result.suggestions:
                feedback_prompt += f"Suggestion: {sugg}\n"

        session_update = state.session.model_copy(update={"critic_retry_count": critic_retry_count})
        audit_update = state.audit.model_copy(update={"audit_feedback": [feedback_prompt]})

        return {
            "status": FlowStatus.ARCHITECT_CRITIC_REJECTED,
            "session": session_update,
            "audit": audit_update,
        }


# ---------------------------------------------------------------------------
#  AuditorNodes
# ---------------------------------------------------------------------------

class AuditorNodes:
    def __init__(self, jules: Any, git: Any, llm_reviewer: Any) -> None:
        self.jules = jules
        self.git = git
        self.llm_reviewer = llm_reviewer

    async def auditor_node(self, state: CycleState) -> dict[str, Any]:
        from src.services.auditor_usecase import AuditorUseCase, UATAuditorUseCase

        if getattr(state, "uat_execution_state", None):
            uat_usecase = UATAuditorUseCase(self.llm_reviewer)
            return dict(await uat_usecase.execute(state))

        usecase = AuditorUseCase(self.jules, self.git, self.llm_reviewer)
        return dict(await usecase.execute(state))


# ---------------------------------------------------------------------------
#  CoderCriticNodes
# ---------------------------------------------------------------------------

class CoderCriticNodes:
    def __init__(self, jules_client: Any) -> None:
        self.jules = jules_client

    async def coder_critic_node(self, state: CycleState) -> dict[str, Any]:
        """Node for Coder Self-Critic phase."""
        from src.services.coder_usecase import CoderUseCase
        from src.utils import logger

        usecase = CoderUseCase(self.jules)
        cycle_id = state.cycle_id
        session_id = state.session.jules_session_name

        logger.info(
            f"[CRITIC] Starting self-critic phase for cycle {cycle_id} on session {session_id}"
        )

        if not session_id:
            logger.error(f"[CRITIC] Failed: No session ID for cycle {cycle_id}")
            return {"status": FlowStatus.FAILED, "error": "No session ID for critic phase"}

        is_final = state.current_phase in {WorkPhase.FINAL_CRITIC, WorkPhase.REFACTORING}
        result = await usecase.run_critic_phase(state, cycle_id, session_id, is_final=is_final)

        if not result:
            logger.warning(
                f"[CRITIC] Critic phase produced no result for cycle {cycle_id}. "
                "Proceeding with existing state."
            )
            return {
                "status": FlowStatus.READY_FOR_AUDIT,
                "session": state.session,
                "branch_name": state.session.branch_name,
                "pr_url": state.session.pr_url,
            }

        pr_url = result.get("pr_url") or state.session.pr_url
        branch_name = result.get("branch_name") or state.session.branch_name

        session_update = state.session.model_copy(
            update={"pr_url": pr_url, "branch_name": branch_name}
        )

        new_status = FlowStatus.COMPLETED

        if pr_url:
            phase_type = "final self critic review" if is_final else "selfcritic review"
            console.print(f"[bold green]PR Point [{phase_type}]:[/bold green] {pr_url}")

        next_phase = WorkPhase.FINAL_CRITIC if is_final else WorkPhase.SELF_CRITIC

        return {
            "status": new_status,
            "session": session_update,
            "branch_name": branch_name,
            "pr_url": pr_url,
            "current_phase": next_phase,
        }
