"""Consolidated fixer/integrator node implementations.

Merged from: global_refactor.py, integration_fixer.py, master_integrator.py
"""

import uuid
from pathlib import Path
from typing import Any

from rich.console import Console

from src.config import settings
from src.enums import FlowStatus
from src.services.integration_usecase import (
    IntegrationUsecase,
    MasterIntegratorClient,
    MaxRetriesExceededError,
)
from src.services.refactor_usecase import RefactorUsecase
from src.state import CycleState, IntegrationState
from src.utils import logger

console = Console()


# ---------------------------------------------------------------------------
#  GlobalRefactorNodes
# ---------------------------------------------------------------------------

class GlobalRefactorNodes:
    """LangGraph node for executing global project refactoring."""

    def __init__(self, usecase: RefactorUsecase) -> None:
        self.usecase = usecase

    async def global_refactor_node(self, state: CycleState) -> dict[str, Any]:
        logger.info("Executing Global Refactor Node...")

        try:
            result = await self.usecase.execute()
        except Exception as e:
            logger.error(f"Global Refactor Node encountered an error: {e}")
            return {"error": str(e)}
        else:
            if result.refactorings_applied:
                logger.info(
                    f"Refactorings successfully applied to {len(result.modified_files)} files."
                )
            else:
                logger.info("No global refactorings applied.")

            committee_update = state.committee.model_copy(update={"is_refactoring": True})
            return {
                "committee": committee_update,
                "status": FlowStatus.POST_AUDIT_REFACTOR,
            }


# ---------------------------------------------------------------------------
#  IntegrationFixerNodes
# ---------------------------------------------------------------------------

class IntegrationFixerNodes:
    def __init__(self, jules_client: Any) -> None:
        self.jules_client = jules_client

    async def integration_fixer_node(self, state: IntegrationState) -> dict[str, Any]:
        """Resolves logical errors after a successful merge."""
        console.print(
            "[bold cyan]Invoking Integration Fixer to resolve logical regressions...[/bold cyan]"
        )

        session_id = state.master_integrator_session_id
        if not session_id:
            session_id = f"integration-fixer-{uuid.uuid4().hex[:6]}"

        prompt = (
            "You are the Integration Fixer. A recent git merge was successful, but the global "
            "unit tests or linters are failing. Please run the validation suite (e.g., `uv run pytest`, "
            "`uv run ruff check .`, `uv run mypy .`), diagnose the logical regressions, and fix them.\n\n"
            "Do not introduce new features. Your only goal is to make the CI/CD pipeline green again."
        )

        try:
            result = await self.jules_client.run_session(
                session_id=session_id,
                prompt=prompt,
                target_files=settings.get_target_files(),
                context_files=settings.get_context_files(),
                require_plan_approval=False,
            )

            if result.get("status") == "running" and result.get("session_name"):
                logger.info(
                    f"Waiting for integration fixer session {result['session_name']} to complete..."
                )
                completion = await self.jules_client.wait_for_completion(result["session_name"])
                if completion.get("status") == "success":
                    console.print("[bold green]Integration fix completed successfully.[/bold green]")
                else:
                    console.print(
                        f"[bold yellow]Integration fix completed with status: {completion.get('status')}[/bold yellow]"
                    )
        except Exception as e:
            console.print(f"[bold red]Integration Fixer Error: {e}[/bold red]")
            return {"master_integrator_session_id": session_id}
        else:
            return {"master_integrator_session_id": session_id}


# ---------------------------------------------------------------------------
#  MasterIntegratorNodes
# ---------------------------------------------------------------------------

class MasterIntegratorNodes:
    """Nodes for the Master Integrator conflict resolution flow."""

    def __init__(self, master_integrator: MasterIntegratorClient | None = None) -> None:
        self.master_integrator = master_integrator or MasterIntegratorClient()
        self.usecase = IntegrationUsecase(self.master_integrator)

    async def master_integrator_node(self, state: IntegrationState) -> dict[str, Any]:
        repo_path = Path.cwd()
        try:
            new_state = await self.usecase.run_integration_loop(state, repo_path)
        except MaxRetriesExceededError:
            pass
        except Exception as e:
            logger.error(f"Master Integrator node encountered an error: {e}")
        else:
            return {
                "master_integrator_session_id": new_state.master_integrator_session_id,
                "unresolved_conflicts": new_state.unresolved_conflicts,
            }

        return {
            "master_integrator_session_id": state.master_integrator_session_id,
            "unresolved_conflicts": state.unresolved_conflicts,
        }
