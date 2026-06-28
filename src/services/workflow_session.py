"""Session start/finalize and PR creation.

Split from workflow.py — part of WorkflowService decomposition.
"""

import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

from src.config import settings
from src.messages import SuccessMessages, ensure_api_key
from src.services.conflict_manager import ConflictManager
from src.state import CycleState
from src.state_manager import StateManager
from src.utils import logger

console = Console()


class WorkflowSessionManager:
    """Session start, finalization and PR creation.

    Mixin class — depends on self being a WorkflowService instance
    that provides self.services, self.builder, self.git.
    """

    async def start_session(self, prompt: str, audit_mode: bool, max_retries: int) -> None:
        from src.services.environment_validator import EnvironmentValidator

        EnvironmentValidator().verify()
        console.rule("[bold magenta]Starting Jules Session[/bold magenta]")

        docs_dir = settings.paths.documents_dir
        spec_files = {
            str(docs_dir / f): (docs_dir / f).read_text(encoding="utf-8")
            for f in settings.architect_context_files
            if (docs_dir / f).exists()
        }

        if audit_mode:
            from src.services.audit_orchestrator import AuditOrchestrator

            jules: Any = self.services.jules
            orch = AuditOrchestrator(jules)
            try:
                result = await orch.run_interactive_session(
                    prompt=prompt,
                    spec_files=spec_files,
                    max_retries=max_retries,
                )
                if result and result.get("pr_url"):
                    console.print(
                        Panel(
                            f"Audit & Implementation Complete.\nPR: {result['pr_url']}",
                            style="bold green",
                        )
                    )
            except Exception:
                console.print("[bold red]Session Failed.[/bold red]")
                logger.exception("Session Failed")
                sys.exit(1)
        else:
            client: Any = self.services.jules
            try:
                result = await client.run_session(
                    session_id=settings.current_session_id,
                    prompt=prompt,
                    files=list(spec_files.keys()),
                )
                if result and result.get("pr_url"):
                    console.print(
                        Panel(
                            f"Implementation Sent.\nPR: {result['pr_url']}",
                            style="bold green",
                        )
                    )
            except Exception:
                console.print("[bold red]Session Failed.[/bold red]")
                logger.exception("Session Failed")
                sys.exit(1)

    async def finalize_session(self, project_session_id: str | None) -> None:
        from src.nodes import GlobalRefactorNodes
        from src.services.environment_validator import EnvironmentValidator
        from src.services.refactor_usecase import RefactorUsecase

        EnvironmentValidator().verify()
        console.rule("[bold cyan]Finalizing Development Session[/bold cyan]")
        ensure_api_key()

        mgr = StateManager()
        manifest = mgr.load_manifest()

        sid = project_session_id or (manifest.project_session_id if manifest else None)
        integration_branch = manifest.integration_branch if manifest else None
        feature_branch = manifest.feature_branch if manifest else None

        if not sid or not integration_branch:
            console.print("[red]No active session found to finalize.[/red]")
            sys.exit(1)

        git = self.git
        try:
            # Checkout integration branch and sync with remote to ensure our archiving commits cleanly
            await git.checkout_branch(integration_branch)
            try:
                await git.pull_changes()
            except Exception as e:
                logger.warning(f"Pull failed before archiving (proceeding anyway): {e}")

            # Merge feature_branch into integration_branch if they differ
            if feature_branch and feature_branch != integration_branch:
                merge_success = await git.safe_merge_with_conflicts(feature_branch)
                if merge_success:
                    await git._run_git(
                        ["commit", "-m", f"Merge {feature_branch} into {integration_branch}"]
                    )
                else:
                    manager = ConflictManager()
                    registry_items = await manager.scan_conflicts(Path.cwd())

                    if manifest:
                        mgr.update_project_state(
                            unresolved_conflicts=[item.model_dump() for item in registry_items]
                        )

                    logger.warning("Merge conflicts recorded. Invoking Master Integrator...")

            # Global Refactoring Loop (CYCLE08)
            refactor_usecase = RefactorUsecase(jules_client=self.services.jules)
            refactor_node = GlobalRefactorNodes(usecase=refactor_usecase)
            refactor_state = CycleState(cycle_id="global_refactor")
            refactor_state.project_session_id = sid
            result = await refactor_node.global_refactor_node(refactor_state)

            if "global_refactor_result" in result:
                await self._handle_global_refactor_result(result, git)

            # We preserve the conflict markers in the repo.

            # Archive and reset for next phase BEFORE creating the PR
            # This ensures the archiving commit is included in the final PR and pushed remotely
            await self._archive_and_reset_state()

            pr_url = await git.create_final_pr(
                integration_branch=integration_branch,
                title=f"Finalize Development Session: {sid}",
                body=f"This PR merges all implemented cycles from session {sid} into main.",
            )
            console.print(SuccessMessages.session_finalized(pr_url))

        except Exception as e:
            console.print(f"[bold red]Finalization failed:[/bold red] {e}")
            sys.exit(1)
