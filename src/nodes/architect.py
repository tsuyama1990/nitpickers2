import abc
import asyncio
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from rich.console import Console

from src.config import settings
from src.enums import FlowStatus
from src.services.git_ops import GitManager
from src.state import CycleState

console = Console()


class BaseNode(BaseModel, abc.ABC):
    """
    Strictly-typed Pydantic base class for all Pipeline Nodes.
    Enforces interface contracts for integration testing without mocks.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        arbitrary_types_allowed=True,
        frozen=True,
    )

    @abc.abstractmethod
    async def __call__(self, state: Any) -> dict[str, Any]: ...


class ArchitectNodes(BaseNode):
    """Architect graph node. Accepts any Any-compatible client."""
    jules: Any
    git: GitManager

    model_config = BaseNode.model_config | {"arbitrary_types_allowed": True}

    async def __call__(self, state: CycleState) -> dict[str, Any]:
        return await self.architect_session_node(state)

    async def _handle_architect_feedback(
        self, state: CycleState, session_id: str
    ) -> dict[str, Any]:
        """Handles the feedback loop from architect_critic_node."""
        feedback = ""
        if state.get("audit_feedback"):
            feedback = "\n".join(state.audit_feedback)
        result = await self.send_audit_feedback_to_session(str(session_id), feedback)
        if result and result.get("pr_url"):
            pr_url = result["pr_url"]
            session_update = state.session.model_copy(update={"pr_url": pr_url})
            return {
                "status": FlowStatus.ARCHITECT_SESSION_COMPLETED,
                "session": session_update,
            }
        return {
            "status": FlowStatus.ARCHITECT_FAILED,
            "error": "Failed to handle architect critic feedback.",
        }

    async def architect_session_node(self, state: CycleState) -> dict[str, Any]:
        """Node for Architect Agent (Jules)."""
        console.print("[bold blue]Starting Architect Session...[/bold blue]")

        # Handle feedback loop from architect_critic_node
        session_id = state.get("project_session_id")
        if state.get("status") == "architect_critic_rejected" and session_id:
            return await self._handle_architect_feedback(state, str(session_id))

        instruction = settings.read_template("ARCHITECT_INSTRUCTION.md")

        n = state.get("requested_cycle_count") or state.get("planned_cycle_count")

        if n:
            instruction = instruction.replace("{{max_cycles}}", str(n))
            instruction += (
                f"\n\nIMPORTANT CONSTRAINT: The development plan MUST be divided into "
                f"exactly {n} implementation cycles."
            )
        else:
            instruction = instruction.replace("{{max_cycles}}", "appropriate number of")

        timestamp = datetime.now(UTC).strftime("%Y%md%H%M%S")
        integration_branch = f"feat/generate-architecture-{timestamp}"

        try:
            await self.git.create_feature_branch(integration_branch)
            console.print(f"[dim]Working on integration branch: {integration_branch}[/dim]")
        except Exception as e:
            console.print(f"[bold red]Failed to setup architect branch: {e}[/bold red]")
            return {"status": FlowStatus.ARCHITECT_FAILED, "error": f"Git checkout failed: {e}"}

        context_files = ["dev_documents/ALL_SPEC.md", "README.md", "README_DEVELOPER.md"]
        from pathlib import Path

        scenario_path = Path("dev_documents/USER_TEST_SCENARIO.md")
        if await asyncio.to_thread(scenario_path.exists):
            context_files.append("dev_documents/USER_TEST_SCENARIO.md")

        result = await self.jules.run_session(
            command="Design the system architecture based on ALL_SPEC.md.",
            session_id=f"architect-{timestamp}",
            prompt=instruction,
            target_files=context_files,
            context_files=[],
            require_plan_approval=False,
        )

        session_name = result.get("session_name")
        if session_name and result.get("status") == "running":
            console.print(
                f"[bold blue]Architect Session {session_name} created. Waiting for completion...[/bold blue]"
            )
            result = await self.jules.wait_for_completion(session_name)

        if result.get("status") == "success" and result.get("pr_url") and session_name:
            pr_url = result["pr_url"]

            session_update = state.session.model_copy(
                update={
                    "integration_branch": integration_branch,
                    "active_branch": integration_branch,
                    "project_session_id": session_name,
                    "pr_url": pr_url,
                }
            )
            # --- Explicit PR Checkpoint Notification ---
            if pr_url:
                console.print(f"[bold green]PR Point [Architect]:[/bold green] {pr_url}")

            return {
                "status": FlowStatus.ARCHITECT_SESSION_COMPLETED,
                "session": session_update,
            }

        if result.get("error"):
            return {"status": FlowStatus.ARCHITECT_FAILED, "error": result.get("error")}

        return {"status": FlowStatus.ARCHITECT_FAILED, "error": "Unknown Jules error or no PR URL"}

    async def send_audit_feedback_to_session(
        self, session_id: str, feedback: str
    ) -> dict[str, Any] | None:
        console.print(
            f"[bold yellow]Sending Audit Feedback to existing Jules session: {session_id}[/bold yellow]"
        )
        try:
            feedback_template = settings.read_template("AUDIT_FEEDBACK_MESSAGE.md")
            feedback_msg = feedback_template.replace("{{feedback}}", feedback)
            await self.jules.send_message(session_id, feedback_msg)
            console.print(
                "[dim]Waiting for Jules to process feedback (expecting IN_PROGRESS)...[/dim]"
            )

            state_transitioned = False
            for attempt in range(12):
                await asyncio.sleep(5)
                current_state = await self.jules.get_session_state(session_id)
                console.print(f"[dim]State check ({attempt + 1}/12): {current_state}[/dim]")

                if current_state in {
                    "IN_PROGRESS",
                    "QUEUED",
                    "PLANNING",
                    "AWAITING_PLAN_APPROVAL",
                    "AWAITING_USER_FEEDBACK",
                    "PAUSED",
                }:
                    state_transitioned = True
                    console.print(
                        "[green]Jules session is now active. Proceeding to monitor...[/green]"
                    )
                    break
                if current_state == "FAILED":
                    console.print("[red]Jules session failed during feedback wait.[/red]")
                    return None

            if not state_transitioned:
                console.print(
                    "[yellow]Warning: Jules session state did not change to IN_PROGRESS after 60s. "
                    "Assuming message received but state lagging, or task finished very quickly.[/yellow]"
                )

            result = await self.jules.wait_for_completion(session_id, expect_new_work=True)

        except Exception as e:
            console.print(
                f"[yellow]Failed to send feedback to existing session: {e}. Creating new session...[/yellow]"
            )
            return None

        if result.get("status") == "success" or result.get("pr_url"):
            return {"pr_url": result.get("pr_url")}

        console.print(
            "[yellow]Jules session finished without new PR. Creating new session...[/yellow]"
        )
        return None
