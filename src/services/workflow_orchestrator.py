"""Pipeline orchestration — Architect phase, full pipeline, integration, QA.

Split from workflow.py — part of WorkflowService decomposition.
"""

import sys
from typing import Any

from langchain_core.runnables import RunnableConfig
from rich.console import Console

from src.config import settings
from src.domain_models import CycleManifest, TracingMetadata
from src.enums import FlowStatus, WorkPhase
from src.messages import SuccessMessages, ensure_api_key
from src.state import CycleState, IntegrationState
from src.state_manager import StateManager
from src.utils import KeepAwake, logger

console = Console()


class WorkflowOrchestrator:
    """Phase orchestration and pipeline execution.

    Mixin class — depends on self being a WorkflowService instance
    that provides self.services, self.builder, self.git.
    """

    def verify_environment_and_observability(self) -> None:
        """Alias for EnvironmentValidator to prevent breaking API."""
        from src.services.environment_validator import EnvironmentValidator

        EnvironmentValidator().verify()

    async def run_gen_cycles(  # noqa: PLR0915
        self, cycles: int, project_session_id: str | None, auto_run: bool = False
    ) -> None:
        from src.services.environment_validator import EnvironmentValidator

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

    async def run_cycle(
        self,
        cycle_id: str | None,
        resume: bool,
        auto: bool,
        start_iter: int,
        project_session_id: str | None,
        parallel: bool = False,
    ) -> None:
        from src.services.environment_validator import EnvironmentValidator

        EnvironmentValidator().verify()
        try:
            # Default to "all" behavior (resume pending) if no ID provided
            if cycle_id is None or cycle_id.lower() == "all":
                await self._run_all_cycles(resume, auto, start_iter, project_session_id, parallel)
                return

            await self._run_single_cycle(cycle_id, resume, auto, start_iter, project_session_id)
        finally:
            pass

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
        from src.services.environment_validator import EnvironmentValidator

        EnvironmentValidator().verify()
        console.rule("[bold cyan]Starting Full Pipeline Orchestration[/bold cyan]")

        await self._run_parallel_coder_phase(project_session_id, parallel, resume=resume)
        await self.run_integration_phase(project_session_id)
        await self.run_qa_phase(project_session_id)

        console.print("[bold green]Full Pipeline Execution Completed Successfully.[/bold green]")

    async def _run_parallel_coder_phase(
        self, project_session_id: str | None, parallel: bool, resume: bool = False
    ) -> None:
        from src.services.async_dispatcher import AsyncDispatcher

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
        from src.services.environment_validator import EnvironmentValidator

        EnvironmentValidator().verify()
        # --- Phase 3: Integration Graph ---
        console.rule("[bold blue]Phase 3: Integration Graph[/bold blue]")
        manifest = self._get_manifest()

        # Aggregate states and branches
        # In this implementation, feature_branch holds the state
        branches_to_merge: list[str] = []
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
        from src.services.environment_validator import EnvironmentValidator

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
        from src.services.async_dispatcher import AsyncDispatcher

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

    async def generate_tutorials(self, project_session_id: str | None) -> None:
        """
        QA Phase: Generate and verify tutorials based on FINAL_UAT.md.
        """
        import asyncio

        from src.services.environment_validator import EnvironmentValidator

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
                    f"QA Tutorials Generated & Verified.\nPR: {final_state.get('pr_url')}",
                    style="bold green",
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
