from pathlib import Path
from typing import Any

from rich.console import Console

from src.config import settings
from src.domain_models import AuditResult
from src.enums import FlowStatus, WorkPhase
from src.services.git_ops import GitManager, workspace_lock
from src.services.jules_client import JulesClient
from src.services.llm_reviewer import LLMReviewer
from src.state import CycleState
from src.state_manager import StateManager

console = Console()


class AuditorUseCase:
    """
    Encapsulates the logic and interactions for the Auditor AI (LLM / Aider).
    """

    def __init__(
        self,
        jules_client: JulesClient,
        git_manager: GitManager,
        llm_reviewer: LLMReviewer,
        sandbox_runner: Any = None,
    ) -> None:
        self.jules = jules_client
        self.git = git_manager
        self.llm_reviewer = llm_reviewer
        self.sandbox = sandbox_runner

    async def _read_files(self, file_paths: list[str]) -> dict[str, str]:
        """Helper to read files from the local filesystem (isolated if worktree set)."""
        import anyio

        result = {}
        for path_str in file_paths:
            p = anyio.Path(path_str)
            # If we are in an isolated worktree, ensure we read from that directory
            if self.git.cwd and not p.is_absolute():
                p = anyio.Path(self.git.cwd) / path_str

            if await p.exists() and await p.is_file():
                try:
                    # We still use the original relative path as the key for LLM consistency
                    result[path_str] = await p.read_text(encoding="utf-8")
                except Exception as e:
                    console.print(f"[yellow]Warning: Could not read {path_str}: {e}[/yellow]")
        return result

    async def execute(self, state: CycleState) -> dict[str, Any]:  # noqa: C901, PLR0915
        """Runs the auditor logic, static analysis, and prepares LLM reviewer feedback."""
        console.print("[bold magenta]Starting Auditor...[/bold magenta]")

        is_refactor_phase = getattr(state, "current_phase", None) == WorkPhase.REFACTORING
        template_name = (
            settings.template_files.final_refactor_instruction
            if is_refactor_phase
            else settings.template_files.coder_critic_instruction
        )

        instruction = settings.get_prompt_content(template_name)
        if not instruction and is_refactor_phase:
            # Fallback if someone hasn't created it yet
            instruction = settings.get_prompt_content(
                settings.template_files.coder_critic_instruction
            )

        if not instruction:
            instruction = "Review this code."

        instruction = instruction.replace("{{cycle_id}}", str(state.cycle_id))

        context_paths = settings.get_context_files()
        architect_instruction = settings.get_template(settings.template_files.architect_instruction)
        if architect_instruction.exists():
            context_paths.append(str(architect_instruction))
        context_docs = await self._read_files(context_paths)

        try:
            async with workspace_lock:
                new_last_audited_commit = state.last_audited_commit
                pr_url = state.pr_url

                if pr_url:
                    console.print(f"[dim]Checking out PR: {pr_url}[/dim]")
                    try:
                        await self.git.checkout_pr(pr_url)
                        console.print("[dim]Successfully checked out PR branch[/dim]")

                        current_commit = await self.git.get_current_commit()
                        last_audited = state.last_audited_commit

                        if current_commit and current_commit == last_audited:
                            console.print(
                                f"[bold yellow]Consistency Check: Hash {current_commit[:7]} matches last audit.[/bold yellow]"
                            )

                            jules_session_id = state.jules_session_name
                            if not jules_session_id:
                                mgr = StateManager()
                                cycle_manifest = mgr.get_cycle(state.cycle_id)
                                if cycle_manifest:
                                    jules_session_id = cycle_manifest.jules_session_id

                            if jules_session_id:
                                try:
                                    jules_status = await self.jules.get_session_state(
                                        jules_session_id
                                    )
                                    TERMINAL_STATES = {"COMPLETED", "FAILED"}

                                    if jules_status not in TERMINAL_STATES:
                                        console.print(
                                            f"[bold yellow]Jules session active ({jules_status}). Waiting for hash advancement...[/bold yellow]"
                                        )
                                        audit_update = state.audit.model_copy(
                                            update={
                                                "audit_result": state.audit_result,
                                                "last_audited_commit": last_audited,
                                            }
                                        )
                                        return {
                                            "status": FlowStatus.WAITING_FOR_JULES,
                                            "audit": audit_update,
                                        }

                                    console.print(
                                        f"[bold yellow]Jules terminal ({jules_status}) without changes. Proceeding to audit.[/bold yellow]"
                                    )
                                except Exception as e:
                                    console.print(
                                        f"[dim]Failed to check Jules session status: {e}[/dim]"
                                    )

                        new_last_audited_commit = current_commit
                    except Exception as e:
                        console.print(f"[yellow]Warning: Could not checkout PR: {e}[/yellow]")
                else:
                    console.print(
                        "[yellow]Warning: No PR URL provided, reviewing current branch[/yellow]"
                    )

                base_branch = state.feature_branch or state.integration_branch or "main"
                if pr_url:
                    try:
                        pr_base = await self.git.get_pr_base_branch(pr_url)
                        if pr_base:
                            console.print(f"[dim]Detected PR base branch: {pr_base}[/dim]")
                            base_branch = pr_base
                    except Exception as e:
                        console.print(
                            f"[yellow]Warning: Could not get PR base branch: {e}[/yellow]"
                        )

                if is_refactor_phase:
                    # Refactor Auditor reviews all application files for overarching architecture review
                    all_target_files = settings.get_target_files()
                    reviewable_files = [str(f) for f in all_target_files]
                else:
                    changed_file_paths = await self.git.get_changed_files(base_branch=base_branch)
                    reviewable_extensions = settings.auditor.reviewable_extensions
                    reviewable_files = [
                        f for f in changed_file_paths if Path(f).suffix in reviewable_extensions
                    ]

                excluded_patterns = settings.auditor.excluded_patterns

                reviewable_files = [
                    f
                    for f in reviewable_files
                    if not any(
                        f.startswith(pattern) or pattern in f for pattern in excluded_patterns
                    )
                ]

                build_artifact_patterns = settings.auditor.build_artifact_patterns

                reviewable_files = [
                    f
                    for f in reviewable_files
                    if not any(pattern in f for pattern in build_artifact_patterns)
                ]

                if reviewable_files:
                    try:
                        filtered_files = []
                        for file_path in reviewable_files:
                            _stdout, _stderr, code, _ = await self.git.runner.run_command(
                                ["git", "check-ignore", "-q", file_path], check=False
                            )
                            if code != 0:
                                filtered_files.append(file_path)
                        reviewable_files = filtered_files
                    except Exception as e:
                        console.print(
                            f"[yellow]Warning: Could not filter gitignored files: {e}[/yellow]"
                        )

                if not reviewable_files:
                    console.print(
                        "[yellow]Warning: No reviewable application files found. The Coder made no changes.[/yellow]"
                    )
                    console.print(
                        "[dim]Checking if the codebase already satisfies the requirements...[/dim]"
                    )
                    # Instead of automatically rejecting, we provide ALL target files to the LLM
                    all_target_files = settings.get_target_files()
                    reviewable_files = [str(f) for f in all_target_files]

                    instruction += "\n\n[SYSTEM NOTE: The Coder made NO changes in this PR. Your job is to verify if the codebase ALREADY FULLY SATISFIES the Requirements. If yes, output -> REVIEW_PASSED. If no, output -> REVIEW_FAILED and specify what needs to be changed.]"

                context_file_names = {str(p) for p in context_paths}
                reviewable_files = [f for f in reviewable_files if f not in context_file_names]

                target_files = await self._read_files(reviewable_files)
        except Exception as e:
            console.print(f"[bold red]Error: Could not determine files to review: {e}[/bold red]")
            raise

        model = settings.agents.auditor_model

        audit_feedback = await self.llm_reviewer.review_code(
            target_files=target_files,
            context_docs=context_docs,
            instruction=instruction,
            model=model,
        )

        if "-> REVIEW_PASSED" in audit_feedback:
            status = "approved"
        elif "-> REVIEW_FAILED" in audit_feedback:
            status = "rejected"
        else:
            status = "rejected"

        result = AuditResult(
            status=status.upper(),
            is_approved=(status == "approved"),
            reason="AI Audit Complete",
            feedback=audit_feedback,
        )

        status_enum = FlowStatus.APPROVED if status == "approved" else FlowStatus.REJECTED

        audit_update = state.audit.model_copy(
            update={
                "audit_result": result,
                "last_audited_commit": new_last_audited_commit,
            }
        )
        return {
            "audit": audit_update,
            "status": status_enum,
        }


class UATAuditorUseCase:
    """
    Dedicated usecase for diagnosing and recovering from dynamic Sandbox/UAT Execution failures.
    Strictly follows the Single Responsibility Principle.
    """

    def __init__(self, llm_reviewer: LLMReviewer) -> None:
        self.llm_reviewer = llm_reviewer

    async def execute(self, state: CycleState) -> dict[str, Any]:
        console.print(
            "[bold magenta]UAT Failure Detected. Initiating Diagnostic Outer Loop...[/bold magenta]"
        )

        instruction = settings.get_prompt_content(settings.template_files.uat_auditor_instruction)
        if not instruction:
            instruction = "You are the Outer Loop Diagnostician. You must strictly output valid JSON matching the FixPlanSchema."
        instruction = instruction.replace("{{cycle_id}}", str(state.cycle_id))
        model = settings.reviewer.smart_model

        if not state.uat_execution_state:
            msg = "UAT Execution state is required for UATAuditorUseCase"
            raise ValueError(msg)

        try:
            fix_plan = await self.llm_reviewer.diagnose_uat_failure(
                uat_state=state.uat_execution_state,
                instruction=instruction,
                model=model,
            )
        except Exception as e:
            console.print(f"[bold red]Diagnostic Loop Failed: {e}[/bold red]")
            return {"status": FlowStatus.REJECTED, "error": str(e)}
        else:
            # We do not approve, we bounce it back to the Coder via RETRY_FIX
            # But we bypass the normal committee loop because this is an execution failure, not a code review failure

            # Increment retry count for the circuit breaker
            state.uat_retry_count += 1

            console.print(
                f"[bold green]Diagnostic complete. Fix plan formulated with {len(fix_plan.patches)} patches.[/bold green]"
            )
            uat_update = state.uat.model_copy(
                update={
                    "current_fix_plan": fix_plan,
                    "uat_execution_state": None,
                    "uat_retry_count": state.uat_retry_count,
                }
            )
            return {
                "uat": uat_update,
                "status": FlowStatus.RETRY_FIX,
                "last_feedback_time": 0,
            }
