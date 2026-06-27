import asyncio
import contextlib
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

import anyio
from langchain_core.runnables import RunnableConfig
from rich.console import Console
from rich.panel import Panel

from src.config import settings
from src.domain_models import CycleManifest, TracingMetadata
from src.enums import FlowStatus, WorkPhase
from src.graph import GraphBuilder
from src.messages import SuccessMessages, ensure_api_key
from src.nodes import GlobalRefactorNodes
from src.process_runner import ProcessRunner
from src.service_container import ServiceContainer
from src.services.async_dispatcher import AsyncDispatcher
from src.services.audit_orchestrator import AuditOrchestrator
from src.services.conflict_manager import ConflictManager
from src.services.environment_validator import EnvironmentValidator
from src.services.git_ops import GitManager
from src.state import CycleState, IntegrationState
from src.state_manager import StateManager
from src.utils import KeepAwake, logger

console = Console()


class WorkflowService:
    def __init__(self, services: ServiceContainer | None = None) -> None:
        self.services = services or ServiceContainer.default()

        self.builder = GraphBuilder(
            self.services,
            self.services.jules,
        )
        self.git = GitManager()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    async def run_gen_cycles(  # noqa: PLR0915
        self, cycles: int, project_session_id: str | None, auto_run: bool = False
    ) -> None:
        EnvironmentValidator().verify()
        with KeepAwake(reason="Generating Architecture and Cycles"):
            console.rule("[bold blue]Architect Phase: Generating Cycles[/bold blue]")

        ensure_api_key()
        graph = self.builder.build_architect_graph()

        initial_state = CycleState(cycle_id=settings.DUMMY_CYCLE_ID)
        initial_state.project_session_id = project_session_id
        initial_state.planned_cycle_count = cycles
        initial_state.requested_cycle_count = cycles

        try:
            thread_id = project_session_id or "architect-session"
            metadata = TracingMetadata(session_id=thread_id, execution_type="architect_phase")
            tracing_config = settings.tracing_service.get_run_config(metadata)

            config = RunnableConfig(
                configurable={"thread_id": thread_id},
                recursion_limit=settings.GRAPH_RECURSION_LIMIT,
                **tracing_config,  # type: ignore[typeddict-item]
            )
            final_state = await graph.ainvoke(initial_state, config)

            if final_state.get("error"):
                console.print(f"[red]Architect Phase Failed:[/red] {final_state.get('error')}")
                sys.exit(1)
            else:
                # final_state can be a dict or object in LangGraph depending on integration
                # It is safest to pull directly from the session sub-model
                session_obj = (
                    final_state.get("session")
                    if isinstance(final_state, dict)
                    else getattr(final_state, "session", None)
                )

                if session_obj:
                    session_id_val = getattr(session_obj, "project_session_id", None)
                    integration_branch = getattr(session_obj, "integration_branch", None)
                else:
                    session_id_val = final_state.get("project_session_id")
                    integration_branch = final_state.get("integration_branch")

                # In new strategy, integration_branch IS the feature branch
                feature_branch = integration_branch

                if session_id_val is None or integration_branch is None or feature_branch is None:
                    msg = "Unexpected None for session_id or branch after Architect phase."
                    raise ValueError(msg)  # noqa: TRY301

                # Create Manifest with Cycles
                mgr = StateManager()
                manifest = mgr.create_manifest(
                    session_id_val,
                    feature_branch=feature_branch,
                    integration_branch=integration_branch,
                )
                manifest.cycles = [
                    CycleManifest(id=f"{i:02}", status="planned") for i in range(1, cycles + 1)
                ]
                mgr.save_manifest(manifest)

                console.print(
                    SuccessMessages.architect_complete(session_id_val, integration_branch)
                )

                if auto_run:
                    console.rule("[bold magenta]Auto-Running All Cycles[/bold magenta]")
                    # Chain execution: run all cycles with resume=False, auto=True (default), start_iter=1
                    await self._run_all_cycles(
                        resume=False,
                        auto=True,
                        start_iter=1,
                        project_session_id=session_id_val,
                    )

        except Exception as e:
            console.print(f"[bold red]Architect execution failed: {e}[/bold red]")
            logger.exception("Architect execution failed")

            # Rollback: Clean up any partial state
            if "session_id_val" in locals() and "mgr" in locals():
                try:
                    logger.warning(
                        f"Rolling back generated cycle plans for session {session_id_val}"
                    )
                    # A proper state cleanup would delete the generated state file or empty the cycle array
                    manifest_to_rollback = mgr.load_manifest()
                    if (
                        manifest_to_rollback
                        and manifest_to_rollback.project_session_id == session_id_val
                    ):
                        manifest_to_rollback.cycles = []
                        mgr.save_manifest(manifest_to_rollback)
                except Exception as rollback_err:
                    logger.error(f"Failed to rollback after error: {rollback_err}")

            sys.exit(1)
        finally:
            pass

    async def run_cycle(
        self,
        cycle_id: str | None,
        resume: bool,
        auto: bool,
        start_iter: int,
        project_session_id: str | None,
        parallel: bool = False,
    ) -> None:
        EnvironmentValidator().verify()
        try:
            # Default to "all" behavior (resume pending) if no ID provided
            if cycle_id is None or cycle_id.lower() == "all":
                await self._run_all_cycles(resume, auto, start_iter, project_session_id, parallel)
                return

            await self._run_single_cycle(cycle_id, resume, auto, start_iter, project_session_id)
        finally:
            pass

    def verify_environment_and_observability(self) -> None:
        """Alias for EnvironmentValidator to prevent breaking API."""
        from src.services.environment_validator import EnvironmentValidator

        EnvironmentValidator().verify()

    async def run_full_pipeline(
        self,
        project_session_id: str | None = None,
        parallel: bool = True,
        resume: bool = False,
    ) -> None:
        """
        Orchestrates the entire 5-Phase pipeline.
        Phase 2: Parallel execution of all planned cycles.
        Phase 3: Integration Graph execution.
        Phase 4: QA/UAT Graph execution.
        """
        EnvironmentValidator().verify()
        console.rule("[bold cyan]Starting Full Pipeline Orchestration[/bold cyan]")

        await self._run_parallel_coder_phase(project_session_id, parallel, resume=resume)
        await self.run_integration_phase(project_session_id)
        await self.run_qa_phase(project_session_id)

        console.print("[bold green]Full Pipeline Execution Completed Successfully.[/bold green]")

    def _get_manifest(self) -> Any:
        mgr = StateManager(project_root=str(settings.paths.workspace_root))
        return mgr.load_manifest()

    async def _run_parallel_coder_phase(
        self, project_session_id: str | None, parallel: bool, resume: bool = False
    ) -> None:
        manifest = self._get_manifest()

        if manifest:
            if not hasattr(manifest, "feature_branch") or not manifest.feature_branch:
                msg = "Manifest missing required feature_branch field"
                raise ValueError(msg)
            if not hasattr(manifest, "integration_branch") or not manifest.integration_branch:
                msg = "Manifest missing required integration_branch field"
                raise ValueError(msg)
            cycles_to_run = [c for c in manifest.cycles if c.status != "completed"]
        else:
            cycles_to_run = [CycleManifest(id=cid) for cid in settings.default_cycles]

        if not cycles_to_run:
            console.print("[yellow]No pending cycles to run.[/yellow]")
            return

        # --- Phase 2: Parallel Coder Graph ---
        console.rule("[bold blue]Phase 2: Parallel Coder Graph[/bold blue]")
        dispatcher = AsyncDispatcher()
        batches = dispatcher.resolve_dag(cycles_to_run, parallel=parallel)
        console.print(
            f"[bold cyan]Parallel execution plan: {[[c.id for c in b] for b in batches]}[/bold cyan]"
        )

        def runner(manifest_c: Any) -> Any:
            return self._run_single_cycle(
                manifest_c.id,
                resume=resume,
                auto=True,
                start_iter=0,
                project_session_id=project_session_id,
            )

        results = await dispatcher.execute_batches(batches, runner)
        if any(isinstance(r, Exception) or r is False for r in results):
            msg = "One or more cycles failed in the parallel phase."
            console.print(f"[bold red]{msg}[/bold red]")
            raise RuntimeError(msg)

    async def run_integration_phase(self, project_session_id: str | None = None) -> None:
        EnvironmentValidator().verify()
        # --- Phase 3: Integration Graph ---
        console.rule("[bold blue]Phase 3: Integration Graph[/bold blue]")
        manifest = self._get_manifest()

        # Aggregate states and branches
        # In this implementation, feature_branch holds the state
        branches_to_merge = []
        if manifest and manifest.feature_branch:
            branches_to_merge.append(manifest.feature_branch)

        integration_state = IntegrationState(branches_to_merge=branches_to_merge)
        integration_graph = self.builder.build_integration_graph()

        thread_id = f"integration-{project_session_id or 'default'}"
        metadata = TracingMetadata(session_id=thread_id, execution_type="integration_phase")
        tracing_config = settings.tracing_service.get_run_config(metadata)
        config = RunnableConfig(
            configurable={"thread_id": thread_id},
            recursion_limit=settings.GRAPH_RECURSION_LIMIT,
            **tracing_config,  # type: ignore[typeddict-item]
        )

        final_integration_state = await integration_graph.ainvoke(integration_state, config)
        if final_integration_state.get("conflict_status") == "failed":
            msg = "Integration Phase Failed: Unresolved conflicts."
            console.print(f"[bold red]{msg}[/bold red]")
            raise RuntimeError(msg)
        console.print("[bold green]Integration Phase Completed Successfully.[/bold green]")

    async def run_qa_phase(self, project_session_id: str | None = None) -> None:
        EnvironmentValidator().verify()
        # --- Phase 4: QA/UAT Graph ---
        console.rule("[bold blue]Phase 4: QA/UAT Graph[/bold blue]")
        qa_graph = self.builder.build_qa_graph()

        initial_qa_state = CycleState(
            cycle_id=settings.dummy_qa_cycle_id if hasattr(settings, "dummy_qa_cycle_id") else "99",
            current_phase=WorkPhase.QA,
            status=FlowStatus.START,
        )
        initial_qa_state.project_session_id = project_session_id

        qa_thread_id = f"qa-{project_session_id or 'default'}"
        qa_metadata = TracingMetadata(session_id=qa_thread_id, execution_type="qa_phase")
        qa_tracing_config = settings.tracing_service.get_run_config(qa_metadata)

        qa_config = RunnableConfig(
            configurable={"thread_id": qa_thread_id},
            recursion_limit=settings.GRAPH_RECURSION_LIMIT,
            **qa_tracing_config,  # type: ignore[typeddict-item]
        )

        final_qa_state = await qa_graph.ainvoke(initial_qa_state, qa_config)
        if final_qa_state.get("status") == "failed" or final_qa_state.get("error"):
            msg = f"QA Phase Failed: {final_qa_state.get('error')}"
            console.print(f"[bold red]{msg}[/bold red]")
            raise RuntimeError(msg)
        console.print("[bold green]QA Phase Completed Successfully.[/bold green]")

    async def _run_all_cycles(
        self,
        resume: bool,
        auto: bool,
        start_iter: int,
        project_session_id: str | None,
        parallel: bool = False,
    ) -> None:
        manifest = self._get_manifest()

        if manifest:
            # We construct instances of CycleManifest for all remaining ones to feed the dispatcher
            cycles_to_run = [c for c in manifest.cycles if c.status != "completed"]
        else:
            cycles_to_run = [CycleManifest(id=cid) for cid in settings.default_cycles]

        cycle_ids = [c.id for c in cycles_to_run]
        console.print(f"[bold cyan]Running Pending Cycles: {cycle_ids}[/bold cyan]")

        if not parallel:
            for idx, cid in enumerate(cycle_ids, 1):
                console.print(
                    f"[bold yellow]Starting Cycle {cid} ({idx}/{len(cycle_ids)})[/bold yellow]"
                )
                await self._run_single_cycle(str(cid), resume, auto, start_iter, project_session_id)
                console.print(
                    f"[bold green]Completed Cycle {cid} ({idx}/{len(cycle_ids)})[/bold green]"
                )
        else:
            dispatcher = AsyncDispatcher()
            batches = dispatcher.resolve_dag(cycles_to_run, parallel=True)
            console.print(
                f"[bold cyan]Parallel execution plan: {[[c.id for c in b] for b in batches]}[/bold cyan]"
            )

            def runner(manifest_c: Any) -> Any:
                return self._run_single_cycle(
                    manifest_c.id, resume, auto, start_iter, project_session_id
                )

            await dispatcher.execute_batches(batches, runner)

        # After all cycles, run QA/Tutorial Generation
        await self.generate_tutorials(project_session_id)

        # Auto-finalize if requested
        if auto:
            await self.finalize_session(project_session_id)

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

    async def _execute_cycle_graph(
        self,
        cycle_id: str,
        start_iter: int,
        resume: bool,
        pid: str | None,
        fb: str | None,
        ib: str | None,
        planned_count: int,
        git_manager: GitManager | None = None,
    ) -> bool:
        """Execute the cycle graph with optional worktree-isolation."""
        from src.graph_nodes import CycleNodes

        # If we have a custom git_manager (worktree-pinned), we create a specialized graph
        if git_manager:
            nodes = CycleNodes(self.builder.jules, git_manager=git_manager)
            builder = GraphBuilder(self.services, self.builder.jules, nodes=nodes)
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

        from src.utils import TraceIdCallbackHandler

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

    def _serialize_state_data(self, state: CycleState | dict[str, Any]) -> dict[str, Any]:
        """Helper to serialize state into a dict."""
        import json

        def pydantic_encoder(obj: Any) -> Any:
            if hasattr(obj, "model_dump"):
                return obj.model_dump(mode="json")
            if hasattr(obj, "dict"):
                return obj.dict()
            if hasattr(obj, "value"):  # Enum
                return obj.value
            msg = f"Object of type {type(obj).__name__} is not JSON serializable"
            raise TypeError(msg)

        try:
            if hasattr(state, "model_dump"):
                return state.model_dump(mode="json")

            # Use json round-trip with fallback encoder
            return cast(dict[str, Any], json.loads(json.dumps(state, default=pydantic_encoder)))
        except Exception as e:
            logger.warning(f"Failed to fully serialize state: {e}")
            # Last-resort: stringify each value individually
            state_data: dict[str, Any] = {}
            if isinstance(state, dict):
                for k, v in state.items():
                    try:
                        state_data[k] = json.loads(json.dumps(v, default=pydantic_encoder))
                    except Exception:
                        state_data[k] = str(v)
            else:
                state_data = {"error": "Serialization failed", "raw": str(state)}
            return state_data

    def _get_llm_optimized_state(self, state: CycleState | dict[str, Any]) -> dict[str, Any]:
        """Truncates the state to prevent RCA context overflow."""
        state_data = self._serialize_state_data(state)

        # Truncate messages to last 10 turns
        session = state_data.get("session")
        if session and isinstance(session, dict):
            msgs = session.get("messages")
            if isinstance(msgs, list) and len(msgs) > 10:
                session["messages"] = msgs[-10:]
                session["_truncated"] = True

        return state_data

    async def _save_failure_snapshot(
        self,
        cycle_id: str,
        state: CycleState | dict[str, Any],
        error_msg: str,
        git_manager: GitManager | None = None,
    ) -> None:
        """Saves a diagnostic snapshot of the system state upon failure."""
        import json
        import time

        timestamp = int(time.time())
        snapshot_file = Path(f"logs/cycles/failure_{cycle_id}_{timestamp}.json")
        snapshot_file.parent.mkdir(parents=True, exist_ok=True)

        # 1. Truncated State Snapshot

        # 2. Truncated Filesystem Snapshot (Git Diff)
        git = git_manager or GitManager()
        raw_status = "N/A"
        try:
            raw_status = await git.get_status()
        except Exception as e:
            raw_status = f"Failed to capture diff: {e}"

        # Limit diff to 1000 lines
        lines = raw_status.splitlines()
        if len(lines) > 1000:
            diff = "\n".join(lines[:1000]) + "\n... [TRUNCATED - 1000 line limit]"
        else:
            diff = raw_status

        import asyncio
        from datetime import UTC, datetime

        from src.utils import current_trace_id

        try:
            # Prepare failure snapshot directory
            cycles_dir = Path("logs/cycles")
            await asyncio.to_thread(cycles_dir.mkdir, parents=True, exist_ok=True)

            snapshot_file = cycles_dir / f"failure_{cycle_id}.json"

            # Prepare minimal state for LLM to avoid context limits
            optimized_state = self._get_llm_optimized_state(state)

            # 2. Truncated Filesystem Snapshot (Git Diff)
            git = git_manager or GitManager()
            try:
                raw_status = await git.get_status()
            except Exception as e:
                raw_status = f"Failed to capture diff: {e}"

            # Limit diff to 1000 lines
            lines = raw_status.splitlines()
            if len(lines) > 1000:
                diff = "\n".join(lines[:1000]) + "\n... [TRUNCATED - 1000 line limit]"
            else:
                diff = raw_status

            # Assemble full diagnostic payload
            diagnostic_data = {
                "timestamp": datetime.now(UTC).isoformat(),
                "cycle_id": cycle_id,
                "trace_id": current_trace_id.get(),
                "error": error_msg,
                "git_diff": diff,
                "state": optimized_state,
            }

            # Save snapshot
            await asyncio.to_thread(snapshot_file.write_text, json.dumps(diagnostic_data, indent=2))

            # Trigger RCAService (also fire-and-forget)
            from src.services.rca_service import RCAService

            rca = RCAService()
            # We don't await this here, just initiate it
            task = asyncio.create_task(rca.analyze_failure(cycle_id, snapshot_file))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

            console.print(
                "[bold magenta]AI Post-Mortem Analysis triggered in background.[/bold magenta]"
            )

        except Exception as e:
            logger.warning(f"Failed to save diagnostic snapshot or perform RCA: {e}")

    def _update_cycle_status(self, cycle_id: str) -> None:
        """Update cycle status to completed."""
        mgr = StateManager()
        if mgr.load_manifest():
            mgr.update_cycle_state(cycle_id, status="completed")

    async def _setup_cycle_workspace(
        self, cycle_id: str, fb: str | None, wt_mgr_cls: Any, git_mgr_cls: Any, lock: Any
    ) -> tuple[Any, Any, Path | None]:
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
        from src.services.git.worktree import GitWorktreeManager
        from src.services.git_ops import GitManager, workspace_lock

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

    async def start_session(self, prompt: str, audit_mode: bool, max_retries: int) -> None:
        EnvironmentValidator().verify()
        console.rule("[bold magenta]Starting Jules Session[/bold magenta]")

        docs_dir = settings.paths.documents_dir
        spec_files = {
            str(docs_dir / f): (docs_dir / f).read_text(encoding="utf-8")
            for f in settings.architect_context_files
            if (docs_dir / f).exists()
        }

        if audit_mode:
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

    async def generate_tutorials(self, project_session_id: str | None) -> None:
        """
        QA Phase: Generate and verify tutorials based on FINAL_UAT.md.
        """
        EnvironmentValidator().verify()
        console.rule("[bold cyan]QA Phase: Tutorial Generation[/bold cyan]")

        docs_dir = settings.paths.documents_dir
        qa_instruction_path = docs_dir / "system_prompts" / "QA_TUTORIAL_INSTRUCTION.md"

        if not await asyncio.to_thread(qa_instruction_path.exists):
            console.print(
                "[yellow]Skipping Tutorial Generation: QA_TUTORIAL_INSTRUCTION.md not found.[/yellow]"
            )
            return

        # Build QA Graph
        graph = self.builder.build_qa_graph()

        # Initial State
        project_session_id = project_session_id or settings.current_session_id
        initial_state = CycleState(
            cycle_id="qa-tutorials",
            current_phase=WorkPhase.QA,
            status=FlowStatus.START,
        )
        initial_state.project_session_id = project_session_id

        thread_id = f"qa-{project_session_id}"
        metadata = TracingMetadata(session_id=thread_id, execution_type="qa_phase")
        tracing_config = settings.tracing_service.get_run_config(metadata)

        config = RunnableConfig(
            configurable={"thread_id": thread_id},
            recursion_limit=settings.GRAPH_RECURSION_LIMIT,
            **tracing_config,  # type: ignore[typeddict-item]
        )

        try:
            console.print("[cyan]Running QA Tutorial Generation Graph...[/cyan]")
            final_state = await graph.ainvoke(initial_state, config)

            audit_res = final_state.get("audit_result")
            if audit_res and getattr(audit_res, "is_approved", False):
                console.print(
                    Panel(
                        f"QA Tutorials Generated & Verified.\nPR: {final_state.get('pr_url')}",
                        style="bold green",
                    )
                )
            elif final_state.get("status") == "max_retries":
                console.print(
                    f"[bold yellow]QA Phase Warning: {final_state.get('error')}[/bold yellow]"
                )
                console.print("[yellow]Proceeding with best-effort results.[/yellow]")
            elif final_state.get("error"):
                console.print(f"[red]QA Phase Failed: {final_state['error']}[/red]")
            else:
                console.print("[yellow]QA Phase completed with uncertain status.[/yellow]")

        except Exception as e:
            console.print(f"[bold red]Tutorial Generation Failed:[/bold red] {e}")
            logger.exception("Tutorial Generation Failed")

    def _get_quality_gate_cmds(self) -> list[list[str]]:
        cmds = []
        if settings.sandbox.lint_check_cmd:
            cmds.append(settings.sandbox.lint_check_cmd)
        if settings.sandbox.type_check_cmd:
            cmds.append(settings.sandbox.type_check_cmd)
        if settings.sandbox.test_cmd:
            cmds.append(settings.sandbox.test_cmd.split())

        if not cmds:
            cmds = settings.sandbox.quality_gate_commands
        return cmds

    async def _handle_global_refactor_result(
        self, result: dict[str, Any], git: "GitManager"
    ) -> None:
        """Helper to handle the result of the global refactoring loop."""
        gr_res = result["global_refactor_result"]
        if not gr_res.refactorings_applied:
            return

        container = ServiceContainer.default()
        runner = (
            container.resolve(ProcessRunner) if hasattr(container, "resolve") else ProcessRunner()
        )
        cmds = self._get_quality_gate_cmds()

        # Execute quality gates in isolated temporary directories
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                # Copy current codebase to temp_dir to validate without affecting workspace yet
                # We want to run the tools in the temp dir
                temp_path = Path(temp_dir)

                # Exclude .git, .venv, etc when copying to save time and avoid issues
                def ignore_func(dir_path: str, contents: list[str]) -> list[str]:
                    return [c for c in contents if c in (".git", ".venv", "venv", "__pycache__")]

                await asyncio.to_thread(
                    shutil.copytree, Path.cwd(), temp_path / "workspace", ignore=ignore_func
                )
                workspace_dir = temp_path / "workspace"

                console.print(
                    "[cyan]Running final quality gates post-refactor in isolated sandbox...[/cyan]"
                )
                for cmd in cmds:
                    # This throws CalledProcessError if it fails
                    await runner.run_command(cmd, cwd=workspace_dir)

            # If we reached here, validations passed. Commit the changes in the actual workspace.
            status_output = await git.get_status()
            if status_output and status_output.strip():
                try:
                    await git.add_all()
                    await git.commit("Global refactoring applied.")
                    console.print("[green]Global refactoring successful and tests passed.[/green]")
                except Exception as commit_err:
                    console.print(
                        f"[bold red]Failed to commit global refactoring: {commit_err}[/bold red]"
                    )
                    await git.reset_hard()
        except Exception as e:
            console.print(
                f"[bold red]Quality gates failed after global refactoring: {e}[/bold red]"
            )
            console.print("[yellow]Reverting refactoring changes...[/yellow]")
            try:
                await git.reset_hard()
            except Exception as reset_err:
                console.print(f"[bold red]Failed to revert changes: {reset_err}[/bold red]")
            console.print(
                "[yellow]Refactoring changes reverted to maintain zero-trust validation.[/yellow]"
            )

    async def finalize_session(self, project_session_id: str | None) -> None:
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
            from src.services.refactor_usecase import RefactorUsecase

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

    async def _archive_and_reset_state(self) -> None:
        """
        Archives current session artifacts to dev_documents/system_prompts_phaseNN
        and resets the state for the next phase safely.
        """
        EnvironmentValidator().verify()
        from src.config import settings

        docs_dir = settings.paths.documents_dir
        if not await asyncio.to_thread(docs_dir.exists):
            return

        next_phase_num = self._get_next_phase_num(docs_dir)
        dir_name = settings.ARCHIVE_DIR_TEMPLATE.format(phase_num=next_phase_num)
        phase_dir = docs_dir / dir_name
        console.print(f"\n[bold cyan]Archiving session artifacts to {phase_dir}...[/bold cyan]")

        try:
            await self._archive_files(docs_dir, phase_dir)
            self._reset_project_state(phase_dir)
            self._prepare_next_phase(docs_dir)
            await self._commit_archived_phase(next_phase_num)
        except Exception as e:
            logger.error(f"Failed during archive and reset state: {e}")
            # Consider rollback if needed, but this is best effort

        console.print("[green]Created fresh, empty ALL_SPEC.md for the next phase.[/green]")
        console.print(f"[green]Ready for Phase {next_phase_num + 1}![/green]")

    def _get_next_phase_num(self, docs_dir: Path) -> int:
        existing_phases = [
            d
            for d in docs_dir.iterdir()
            if d.is_dir() and d.name.startswith("system_prompts_phase")
        ]
        nums = []
        for d in existing_phases:
            with contextlib.suppress(IndexError, ValueError):
                nums.append(int(d.name.split("_phase")[1]))
        return max(nums) + 1 if nums else 1

    async def _safe_move_item(self, src: Path, dest: Path) -> None:
        if not await asyncio.to_thread(src.exists):
            return
        await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
        try:
            await self.git._run_git(
                ["mv", str(src), str(dest)]
            )  # Keeping _run_git for mv as there's no public method yet
        except Exception:
            try:
                await asyncio.to_thread(src.replace, dest)
            except OSError:
                await asyncio.to_thread(shutil.move, str(src), str(dest))

    async def _archive_files(self, docs_dir: Path, phase_dir: Path) -> None:
        sys_prompts_dir = docs_dir / "system_prompts"
        if await asyncio.to_thread(sys_prompts_dir.exists):
            await self._safe_move_item(sys_prompts_dir, phase_dir)
        else:
            await asyncio.to_thread(phase_dir.mkdir, parents=True, exist_ok=True)

        await self._safe_move_item(docs_dir / "ALL_SPEC.md", phase_dir / "ALL_SPEC.md")
        await self._safe_move_item(
            docs_dir / "USER_TEST_SCENARIO.md", phase_dir / "USER_TEST_SCENARIO.md"
        )

        tutorials_dir = Path.cwd() / "tutorials"
        if tutorials_dir.exists():
            for item in tutorials_dir.iterdir():
                await self._safe_move_item(item, phase_dir / "tutorials" / item.name)
            await anyio.Path(tutorials_dir).mkdir(exist_ok=True)

        templates_dir = settings.paths.templates
        if templates_dir.exists():
            for cycle_dir in sorted(
                [d for d in templates_dir.iterdir() if d.is_dir() and d.name.startswith("CYCLE")]
            ):
                await self._safe_move_item(cycle_dir, phase_dir / "templates" / cycle_dir.name)

    def _reset_project_state(self, phase_dir: Path) -> None:
        state_mgr = StateManager()
        if state_mgr.STATE_FILE.exists():
            shutil.copy2(str(state_mgr.STATE_FILE), str(phase_dir / "project_state.json"))
            state_mgr.STATE_FILE.unlink()
            console.print("Project state reset (project_state.json archived and removed).")

    def _prepare_next_phase(self, docs_dir: Path) -> None:
        (docs_dir / "ALL_SPEC.md").touch()
        (docs_dir / "USER_TEST_SCENARIO.md").touch()
        (docs_dir / "system_prompts").mkdir(exist_ok=True)

    async def _commit_archived_phase(self, next_phase_num: int) -> None:
        msg = settings.ARCHIVE_COMMIT_MESSAGE.format(phase_num=next_phase_num)
        try:
            await self.git.add_all()
            await self.git.commit(msg)
        except Exception as e:
            logger.warning(f"Failed to commit archive: {e}")
