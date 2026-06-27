import asyncio
from typing import Any

from rich.console import Console
from rich.panel import Panel

from src.config import settings
from src.services.plan_auditor import PlanAuditor
from src.utils import logger

console = Console()


class AuditOrchestrator:
    """
    Orchestrates the interactive planning loop between Jules and PlanAuditor.
    Uses SDK-based methods for all API interactions.
    """

    def __init__(
        self,
        jules_client: Any,
        plan_auditor: PlanAuditor | None = None,
    ) -> None:
        self.jules = jules_client
        if not self.jules:
            msg = "Any must be injected into AuditOrchestrator"
            raise ValueError(msg)
        self.auditor = plan_auditor or PlanAuditor()

    async def run_interactive_session(
        self, prompt: str, spec_files: dict[str, str], max_retries: int = 3
    ) -> dict[str, Any]:
        """
        Starts a session with plan approval requirement and manages the audit loop.
        """
        console.print(Panel("[bold cyan]Starting AI-on-AI Audit Session[/bold cyan]", expand=False))

        file_paths = list(spec_files.keys())

        session_data = await self.jules.run_session(
            session_id=settings.current_session_id,
            prompt=prompt,
            files=file_paths,
            require_plan_approval=True,
        )

        session_name = session_data["session_name"]
        console.print(f"[green]Session Created: {session_name}[/green]")

        retry_count = 0
        current_plan_id = None

        while retry_count <= max_retries:
            console.print(f"\n[bold yellow]--- Audit Round {retry_count + 1} ---[/bold yellow]")
            console.print("[dim]Waiting for Jules to generate a plan...[/dim]")

            if current_plan_id:
                plan_details = await self._wait_for_new_plan(session_name, current_plan_id)
            else:
                plan_details = await self._wait_for_first_plan(session_name)

            if not plan_details:
                t_msg = "Timed out waiting for plan generation."
                raise TimeoutError(t_msg)

            plan_id = plan_details.get("planId")
            current_plan_id = plan_id
            console.print(f"[blue]Plan Generated (ID: {plan_id})[/blue]")

            audit_result = await self.auditor.audit_plan(
                plan_details, spec_files, phase="architect"
            )

            style = "green" if audit_result.status == "APPROVED" else "red"
            console.print(
                Panel(
                    f"Status: {audit_result.status}\nReason: {audit_result.reason}",
                    title="Audit Result",
                    border_style=style,
                )
            )

            if audit_result.status == "APPROVED":
                console.print(
                    "[bold green]Plan Approved. Proceeding to implementation...[/bold green]"
                )
                if plan_id:
                    await self.jules.approve_plan(session_name, str(plan_id))
                result = await self.jules.wait_for_completion(session_name, expect_new_work=True)
                return dict(result)

            retry_count += 1
            if retry_count > max_retries:
                console.print("[bold red]Max retries exceeded. Aborting session.[/bold red]")
                r_msg = "Max audit retries exceeded."
                raise RuntimeError(r_msg)

            feedback = audit_result.feedback or audit_result.reason
            feedback_prompt = (
                f"Your plan was REJECTED by the Lead Architect.\n"
                f"Reason: {audit_result.reason}\n"
                f"Instruction: {feedback}\n"
                f"Please revise the plan accordingly."
            )

            console.print(f"[magenta]Sending Feedback to Jules:[/magenta] {feedback}")
            await self.jules.send_message(session_name, feedback_prompt)

        u_msg = "Session ended unexpectedly."
        raise RuntimeError(u_msg)

    async def _find_plan_in_activities(
        self, session_name: str, skip_plan_id: str | None = None
    ) -> dict[str, Any] | None:
        """Fetch activities and find a planGenerated activity.

        Args:
            session_name: Session resource name.
            skip_plan_id: If set, skip plans with this ID (for finding new plans).

        Returns:
            planGenerated details dict or None.
        """
        try:
            activities = await self.jules.list_activities(session_name)
            for act in activities:
                if act.plan_generated:
                    plan_data = act.plan_generated
                    plan = plan_data.get("plan", {})
                    plan_id = plan.get("id") or plan_data.get("planId")
                    if skip_plan_id and plan_id == skip_plan_id:
                        continue
                    return dict(plan_data)
        except Exception as e:
            logger.warning(f"Failed to fetch activities for plan: {e}")
        return None

    async def _wait_for_first_plan(
        self, session_name: str, timeout_seconds: int = 300
    ) -> dict[str, Any] | None:
        """Poll until a first plan appears in activities."""
        base_delay = 10
        max_delay = 60
        current_delay = base_delay

        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    result = await self._find_plan_in_activities(session_name)
                    if result:
                        return result
                    await asyncio.sleep(current_delay)
                    current_delay = min(current_delay * 2, max_delay)
        except TimeoutError:
            return None

    async def _wait_for_new_plan(
        self, session_name: str, current_plan_id: str, timeout_seconds: int = 300
    ) -> dict[str, Any] | None:
        """Helper to poll until a plan with a different ID appears."""
        console.print("[dim]Waiting for revised plan...[/dim]")

        base_delay = 10
        max_delay = 60
        current_delay = base_delay

        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    result = await self._find_plan_in_activities(
                        session_name, skip_plan_id=current_plan_id
                    )
                    if result:
                        return result
                    await asyncio.sleep(current_delay)
                    current_delay = min(current_delay * 2, max_delay)
        except TimeoutError:
            return None
