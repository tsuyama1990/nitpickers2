"""Single cycle execution with worktree isolation.

Split from workflow.py — part of WorkflowService decomposition.
"""

from typing import Any

from langchain_core.runnables import RunnableConfig
from rich.console import Console

from src.config import settings
from src.domain_models import TracingMetadata
from src.enums import FlowStatus
from src.messages import SuccessMessages, ensure_api_key
from src.state import CycleState
from src.state_manager import StateManager
from src.utils import KeepAwake, logger

console = Console()


class WorkflowCycleExecutor:
    """Single cycle execution with worktree isolation.

    Mixin class — depends on self being a WorkflowService instance
    that provides self.services, self.builder, self.git, self._background_tasks.
    """

    def _check_cycle_completion(self, cycle_id: str) -> bool:
        """Check if cycle is already completed."""
        mgr = StateManager()
        manifest = mgr.load_manifest()
        if manifest:
            cycle = next((c for c in manifest.cycles if c.id == cycle_id), None)
            if cycle and cycle.status == "completed":
                console.print(f"[yellow]Cycle {cycle_id} is already completed. Skipping.[/yellow]")
                return True
        return False

    def _get_manifest(self) -> Any:
        mgr = StateManager(project_root=str(settings.paths.workspace_root))
        return mgr.load_manifest()

    def _update_cycle_status(self, cycle_id: str) -> None:
        """Update cycle status to completed."""
        mgr = StateManager()
        if mgr.load_manifest():
            mgr.update_cycle_state(cycle_id, status="completed")

    async def _setup_cycle_workspace(
        self, cycle_id: str, fb: str | None, wt_mgr_cls: Any, git_mgr_cls: Any, lock: Any
    ) -> tuple[Any, Any, Any | None]:
        """Setup an isolated worktree for the cycle."""
        wt_mgr = wt_mgr_cls()
        cycle_git_manager = None
        worktree_path = None

        async with lock:
            if fb:
                # Sync branch in main repo first
                main_git = git_mgr_cls()
                try:
                    await main_git.checkout_branch(fb)
                    await main_git.pull_changes()
                except Exception as e:
                    logger.warning(f"Branch preparation warning: {e}")

                worktree_path = await wt_mgr.create_worktree(cycle_id, fb)
                cycle_git_manager = git_mgr_cls(cwd=worktree_path)

        return wt_mgr, cycle_git_manager, worktree_path

    async def _execute_cycle_graph(
        self,
        cycle_id: str,
        start_iter: int,
        resume: bool,
        pid: str | None,
        fb: str | None,
        ib: str | None,
        planned_count: int,
        git_manager: "Any | None" = None,
    ) -> bool:
        """Execute the cycle graph with optional worktree-isolation."""
        from src.graph_nodes import CycleNodes
        from src.utils import TraceIdCallbackHandler

        # If we have a custom git_manager (worktree-pinned), we create a specialized graph
        if git_manager:
            nodes = CycleNodes(self.builder.jules, git_manager=git_manager)
            # Re-create builder with custom nodes
            from src.graph import GraphBuilder as _GraphBuilder

            builder = _GraphBuilder(self.services, self.builder.jules, nodes=nodes)
            graph = builder.build_coder_graph()
        else:
            graph = self.builder.build_coder_graph()
        state = CycleState(cycle_id=cycle_id)
        state.iteration_count = start_iter
        state.resume_mode = resume
        state.project_session_id = pid
        state.feature_branch = fb
        state.integration_branch = ib
        state.planned_cycle_count = planned_count

        thread_id = f"cycle-{cycle_id}-{state.project_session_id}"
        metadata = TracingMetadata(
            session_id=thread_id, execution_type="cycle_phase", git_branch=fb
        )
        tracing_config = settings.tracing_service.get_run_config(metadata)

        config = RunnableConfig(
            configurable={
                "thread_id": thread_id,
                "cycle_id": cycle_id,
            },
            recursion_limit=settings.GRAPH_RECURSION_LIMIT,
            callbacks=[TraceIdCallbackHandler()],
            **tracing_config,  # type: ignore[typeddict-item]
        )
        final_state = await graph.ainvoke(state, config)

        if final_state.get("error"):
            console.print(f"[red]Cycle {cycle_id} Failed:[/red] {final_state['error']}")
            await self._save_failure_snapshot(
                cycle_id, final_state, str(final_state["error"]), git_manager
            )
            return False

        if final_state.get("status") == FlowStatus.REQUIRES_PIVOT:
            console.print(
                f"[bold yellow]Cycle {cycle_id} requires a pivot. Falling back to Architect Phase.[/bold yellow]"
            )
            await self.run_gen_cycles(cycles=planned_count, project_session_id=pid, auto_run=False)
            # Re-run the current cycle after a pivot using the new architected plan
            await self._execute_cycle_graph(
                cycle_id, start_iter, resume, pid, fb, ib, planned_count
            )
            return True

        console.print(SuccessMessages.cycle_complete(cycle_id, f"{int(cycle_id) + 1:02}"))
        return True

    async def _run_single_cycle(
        self,
        cycle_id: str,
        resume: bool,
        auto: bool,
        start_iter: int,
        project_session_id: str | None,
    ) -> bool | None:
        from src.utils import current_cycle_id, setup_cycle_logging

        current_cycle_id.set(cycle_id)
        setup_cycle_logging(cycle_id)

        if self._check_cycle_completion(cycle_id):
            return None

        with KeepAwake(reason=f"Running Implementation Cycle {cycle_id}"):
            console.rule(f"[bold green]Coder Phase: Cycle {cycle_id}[/bold green]")

        ensure_api_key()

        if auto:
            settings.auto_approve = True

        root_to_use = str(settings.paths.workspace_root)
        console.print(
            f"[bold blue]DEBUG: Cycle {cycle_id} initializing StateManager with root: {root_to_use}[/bold blue]"
        )
        mgr = StateManager(project_root=root_to_use)
        manifest = mgr.load_manifest()

        if not manifest:
            msg = "No active session found. Run gen-cycles first."
            console.print(f"[red]{msg}[/red]")
            raise RuntimeError(msg)

        pid = project_session_id or manifest.project_session_id
        ib = manifest.integration_branch
        fb = manifest.feature_branch
        planned_count = len(manifest.cycles)

        # -- Isolation Protocol (Git Worktrees) --
        from src.services.git_ops import GitManager, GitWorktreeManager, workspace_lock

        wt_mgr, cycle_git_manager, worktree_path = await self._setup_cycle_workspace(
            cycle_id, fb, wt_mgr_cls=GitWorktreeManager, git_mgr_cls=GitManager, lock=workspace_lock
        )

        try:
            success = await self._execute_cycle_graph(
                cycle_id,
                start_iter,
                resume,
                pid,
                fb,
                ib,
                planned_count,
                git_manager=cycle_git_manager,
            )
        except Exception as e:
            console.print(f"[bold red]Cycle {cycle_id} execution failed.[/bold red]")
            logger.exception("Cycle execution failed")
            await self._save_failure_snapshot(cycle_id, {}, str(e), git_manager=cycle_git_manager)
            return False
        else:
            if success:
                self._update_cycle_status(cycle_id)
                return True
            return False
        finally:
            if wt_mgr and worktree_path:
                try:
                    async with workspace_lock:
                        await wt_mgr.remove_worktree(cycle_id)
                except Exception as e:
                    logger.warning(f"Worktree cleanup failed for cycle {cycle_id}: {e}")
