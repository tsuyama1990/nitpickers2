import asyncio
import re
from datetime import UTC, datetime
from typing import Any

from rich.console import Console

from src.config import settings
from src.domain_models import CycleManifest
from src.enums import FlowStatus, WorkPhase
from src.services.git_ops import workspace_lock
from src.services.jules_client import JulesClient
from src.state import CycleState
from src.state_manager import StateManager
from src.utils import logger

console = Console()

# Jules API states that mean "session is still active/in-flight"
_ACTIVE_STATES = {
    "IN_PROGRESS",
    "QUEUED",
    "PLANNING",
    "AWAITING_PLAN_APPROVAL",
    "AWAITING_USER_FEEDBACK",
    "PAUSED",
}

# Jules API states where an existing session can receive new messages
_REUSABLE_STATES = {
    "IN_PROGRESS",
    "COMPLETED",
    "AWAITING_USER_FEEDBACK",
    "PAUSED",
    "PLANNING",
    "QUEUED",
    "STATE_UNSPECIFIED",
}


class CoderUseCase:
    """
    Encapsulates the logic and interactions with the Coder AI (Jules).
    """

    def __init__(self, jules_client: JulesClient) -> None:
        if not jules_client:
            msg = "JulesClient must be injected into CoderUseCase"
            raise ValueError(msg)
        self.jules = jules_client

    # ------------------------------------------------------------------ #
    #  Public entry point                                                  #
    # ------------------------------------------------------------------ #

    async def execute(self, state: CycleState) -> dict[str, Any]:  # noqa: C901, PLR0915
        """Routes the coder session through its many possible modes."""
        cycle_id = state.cycle_id
        iteration = state.iteration_count
        current_phase = state.current_phase
        phase_label = "REFACTORING" if current_phase == WorkPhase.REFACTORING else "CODER"

        mgr = StateManager()
        cycle_manifest = mgr.get_cycle(cycle_id)

        jules_session_name: str | None = None
        result: dict[str, Any] | None = None

        # --- A. Attempt to identify/wait for an EXISTING session ---
        if (state.status == FlowStatus.WAIT_FOR_JULES_COMPLETION or state.resume_mode) and (
            cycle_manifest and cycle_manifest.jules_session_id and cycle_manifest.jules_session_id != "null"
        ):
            jules_session_name = cycle_manifest.jules_session_id
            console.print(
                f"[bold blue]Waiting/Resuming Jules Session: {jules_session_name}[/bold blue]"
            )
            try:
                # Section A: Resume/Wait. We don't expect new work yet;
                # we just want to know if Jules is already done or still working.
                result = await self.jules.wait_for_completion(
                    jules_session_name, expect_new_work=False
                )

                if not (result.get("status") == "success" or result.get("pr_url")):
                    console.print(
                        "[yellow]Existing session did not produce PR. Restarting...[/yellow]"
                    )
                    result = None
            except Exception as e:
                console.print(f"[yellow]Wait/Resume failed: {e}. Starting new session.[/yellow]")
                result = None

        # --- B. Handle session REUSE (Retry Fix or Post-Audit Refactor) ---
        # We attempt reuse if the session already exists and we're in a status that suggests continuing work.
        if not result and cycle_manifest and cycle_manifest.jules_session_id:
            SHOULD_REUSE_STATUSES = {
                FlowStatus.RETRY_FIX,
                FlowStatus.REJECTED,
                FlowStatus.POST_AUDIT_REFACTOR,
                FlowStatus.TDD_FAILED,
                FlowStatus.START,
                None,
            }
            if state.status in SHOULD_REUSE_STATUSES:
                reuse_result = await self._try_reuse_session(cycle_manifest, state)
                if reuse_result:
                    jules_session_name = cycle_manifest.jules_session_id
                    result = reuse_result

        # --- B2. Special Case: Resuming a session that is already COMPLETED ---
        if not result and cycle_manifest and cycle_manifest.jules_session_id and cycle_manifest.jules_session_id != "null":
            if state.status in {FlowStatus.START, None}:
                # If we are in START and have a session ID, it means we are resuming.
                # If the session is already COMPLETED, we should just get the result.
                session_state = await self.jules.get_session_state(cycle_manifest.jules_session_id)
                if session_state == "COMPLETED":
                    console.print(f"[bold blue]Session {cycle_manifest.jules_session_id} is COMPLETED. Retrieving result...[/bold blue]")
                    result = await self.jules.wait_for_completion(cycle_manifest.jules_session_id, expect_new_work=False)

        # --- C. Launch NEW session ---
        if not result:
            console.print(
                f"[bold green]Starting {phase_label} Session for Cycle {cycle_id} "
                f"(Iteration {iteration})...[/bold green]"
            )
            try:
                async with workspace_lock:
                    # --- Branch Resolution ---
                    # We always prefer to continue on the existing feature branch if it exists.
                    target_branch = cycle_manifest.branch_name if (cycle_manifest and cycle_manifest.branch_name) else None
                    if not target_branch and state.feature_branch:
                        target_branch = state.feature_branch

                    instruction = self._build_instruction(
                        cycle_id, current_phase, state, cycle_manifest
                    )
                    target_files = settings.get_target_files()
                    context_files = settings.get_context_files()

                    import uuid

                    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
                    prefix = "refactor" if current_phase == WorkPhase.REFACTORING else "coder"
                    # Use timestamp + UUID to absolutely ensure collision avoidance across parallel phases
                    session_req_id = f"{prefix}-cycle-{cycle_id}-iter-{iteration}-{timestamp}-{uuid.uuid4().hex[:6]}"

                    jules_session_name, result = await self._run_jules_session(
                        session_req_id, instruction, target_files, context_files, cycle_id, mgr, branch=target_branch
                    )

                # --- Outside lock, wait for completion if running ---
                if result.get("status") == "running" and jules_session_name:
                    console.print(
                        f"[bold blue]Session {jules_session_name} created. Waiting for completion...[/bold blue]"
                    )
                    result = await self.jules.wait_for_completion(jules_session_name)
                    if result and (result.get("status") == "success" or result.get("pr_url")):
                        # Important: capture the commit after the implementation finishes
                        await self._update_last_processed_commit(state, result.get("branch_name"))

            except Exception as e:
                console.print(f"[red]{phase_label} Session Failed: {e}[/red]")
                return await self._handle_session_failure(cycle_manifest, cycle_id, str(e), mgr)

        # --- D. Post-Session Processing (Success Handling & Self-Critic) ---
        if result and (result.get("status") == "success" or result.get("pr_url")):
            is_post_audit_refactor = state.status in {
                FlowStatus.POST_AUDIT_REFACTOR,
                FlowStatus.READY_FOR_FINAL_CRITIC,
            }

            if cycle_manifest:
                mgr.update_cycle_state(cycle_id, session_restart_count=0)

            # --- Phase Decoupling ---
            # We no longer run the critic inline. Instead, we return a status that
            # triggers the dedicated Self-Critic node in the graph.
            # This ensures discrete PR checkpoints for "Initial Coder" and "Self-Critic".

            # Default: initial implementation leads to self-critic
            target_status = FlowStatus.READY_FOR_SELF_CRITIC

            if state.status == FlowStatus.RETRY_FIX:
                # If we just fixed an audit rejection, go straight back to audit (skip self-critic)
                target_status = FlowStatus.READY_FOR_AUDIT

            if is_post_audit_refactor or getattr(state, "final_fix", False):
                # If we just finished a polish/refactor, we move to FINAL critic review
                target_status = FlowStatus.READY_FOR_FINAL_CRITIC

            if state.status == FlowStatus.READY_FOR_FINAL_CRITIC:
                # If we just finished the final critic phase itself, we are done
                target_status = FlowStatus.COMPLETED

            # Extract final PR and branch info
            pr_val = result.get("pr_url") or (cycle_manifest.pr_url if cycle_manifest else None)
            branch_val = result.get("branch_name") or (
                cycle_manifest.branch_name if cycle_manifest else None
            )

            session_updates = {}
            if jules_session_name:
                session_updates["jules_session_name"] = jules_session_name
            if pr_val:
                session_updates["pr_url"] = pr_val
            if branch_val:
                session_updates["branch_name"] = branch_val

            # Reset self-critic flag if this is a major implementation.
            # We preserve it if we are fixing a structural (TDD) error or an auditor rejection
            # to avoid redundant self-critic reviews and proceed directly to the next phase.
            preserve_flag_statuses = {FlowStatus.TDD_FAILED, FlowStatus.RETRY_FIX}
            new_self_critic_completed = state.self_critic_completed if state.status in preserve_flag_statuses else False

            session_update = (
                state.session.model_copy(update={**session_updates, "self_critic_completed": new_self_critic_completed})
                if session_updates or new_self_critic_completed != state.self_critic_completed
                else state.session
            )

            # --- Final Verification: If we reused a session, ensure the commit hash is updated in state ---
            if branch_val and not state.last_processed_commit:
                await self._update_last_processed_commit(state, branch_val)

            # Update the cycle's feature branch if a new one was created by Jules
            if cycle_manifest:
                mgr.update_cycle_state(
                    cycle_id,
                    session_restart_count=0,
                    pr_url=pr_val,
                    branch_name=branch_val,
                    status=target_status, # Persist status for resume support
                    self_critic_completed=new_self_critic_completed
                )

            # --- Explicit PR Checkpoint Notification ---
            if pr_val:
                if state.status == FlowStatus.POST_AUDIT_REFACTOR:
                    checkpoint_label = "refactoring/polish"
                elif state.status == FlowStatus.RETRY_FIX:
                    checkpoint_label = "audit feedback response"
                elif current_phase == WorkPhase.REFACTORING:
                    checkpoint_label = "global refactoring"
                else:
                    checkpoint_label = "coder instruction"

                console.print(f"[bold green]PR Point [{checkpoint_label}]:[/bold green] {pr_val}")

            logger.info(f"CoderUseCase execute complete. Returning status: {target_status}")
            return {
                "status": target_status,
                "session": session_update,
                "branch_name": branch_val,
                "pr_url": pr_val,
                "self_critic_completed": new_self_critic_completed,
            }

        # --- E. Failure Handling ---
        if result and result.get("status") == "failed":
            return await self._handle_session_failure(
                cycle_manifest, cycle_id, result.get("error", "Unknown error"), mgr
            )

        return {"status": FlowStatus.FAILED, "error": "Jules failed to produce PR"}

    def _build_instruction(  # noqa: C901
        self,
        cycle_id: str,
        current_phase: WorkPhase | str | None,
        state: CycleState,
        cycle_manifest: CycleManifest | None,
    ) -> str:
        """Assemble the Jules instruction prompt, injecting feedback when retrying."""
        if state.status == FlowStatus.POST_AUDIT_REFACTOR:
            instruction = settings.get_prompt_content(
                settings.template_files.post_audit_refactor_instruction
            )
        elif current_phase == WorkPhase.REFACTORING:
            instruction = settings.get_prompt_content(
                settings.template_files.final_refactor_instruction
            )
        else:
            instruction = settings.get_prompt_content(settings.template_files.coder_instruction)

        if not instruction:
            instruction = "Implement the requested changes."

        # TDD phase injection
        if state.test.tdd_phase == "red":
            instruction += "\n\nCRITICAL TDD ENFORCEMENT:\nYou are in the 'RED' phase of Test-Driven Development. You MUST write FAILING TESTS ONLY. Do NOT write or modify any implementation code yet. Your goal is to produce a test that fails against the current implementation to prove the bug exists or the feature is missing."
        elif state.test.tdd_phase == "green":
            instruction += "\n\nCRITICAL TDD ENFORCEMENT:\nYou are in the 'GREEN' phase of Test-Driven Development. You must now implement the feature or bugfix to make the newly written failing tests pass. Do not modify the tests themselves unless they are syntactically invalid."

        # Anti-patterns memory injection
        if state.committee.anti_patterns_memory:
            anti_patterns = "\n- ".join(state.committee.anti_patterns_memory)
            instruction += f"\n\nIMPORTANT PREVIOUS ATTEMPTS TO AVOID:\nThe following approaches have failed in this cycle and must NOT be repeated:\n- {anti_patterns}"

        instruction = instruction.replace("{{cycle_id}}", str(cycle_id))

        last_audit = state.audit_result
        if state.status == FlowStatus.RETRY_FIX and last_audit and last_audit.feedback:
            instruction += "\n\n" + self._build_feedback_injection(
                last_audit.feedback, cycle_manifest.pr_url if cycle_manifest else None
            )

        if state.status == FlowStatus.TDD_FAILED and state.error:
            instruction += "\n\n" + self._build_feedback_injection(
                state.error, cycle_manifest.pr_url if cycle_manifest else None
            )

        if state.status == FlowStatus.RETRY_FIX and state.current_fix_plan:
            fix_plan_text = (
                f"## Automated UAT Diagnostic Fix Plan\n"
                f"A recent execution failure was diagnosed by the Outer Loop Auditor.\n"
                f"**Defect Description:** {state.current_fix_plan.defect_description}\n\n"
            )
            for patch in state.current_fix_plan.patches:
                fix_plan_text += (
                    f"**Target File:** `{patch.target_file}`\n"
                    f"**Required Changes:**\n```\n{patch.git_diff_patch}\n```\n\n"
                )
            fix_plan_text += "Please implement these exact changes immediately."
            instruction += "\n\n" + self._build_feedback_injection(
                fix_plan_text, cycle_manifest.pr_url if cycle_manifest else None
            )

        return str(instruction)

    async def _try_reuse_session(  # noqa: C901
        self, cycle_manifest: CycleManifest | None, state: CycleState
    ) -> dict[str, Any] | None:
        """Attempt to send audit feedback to an existing session instead of starting fresh."""
        # Reuse for Retry Fix (Audit failed), Post-Audit Refactor (Audit passed), TDD failure, or Cold Start
        REUSABLE_STATUSES = {
            FlowStatus.RETRY_FIX,
            FlowStatus.REJECTED,
            FlowStatus.POST_AUDIT_REFACTOR,
            FlowStatus.TDD_FAILED,
            FlowStatus.START,
            FlowStatus.READY_FOR_AUDIT,  # Safeguard for loopbacks
            None,
        }

        is_final_fix = getattr(state, "final_fix", False)

        if not (
            (state.status in REUSABLE_STATUSES or is_final_fix)
            and cycle_manifest
            and cycle_manifest.jules_session_id
            and cycle_manifest.jules_session_id != "null"
        ):
            return None

        # Check session state
        session_state = await self.jules.get_session_state(cycle_manifest.jules_session_id)
        if session_state not in _REUSABLE_STATES:
            # --- Branch-Centric Fix: Silently Fallback to New Session ---
            # Instead of a warning, we log an info message that we are continuing on the same branch.
            reason = "terminal" if session_state == "FAILED" else "unexpected"
            logger.info(
                f"Session {cycle_manifest.jules_session_id} is in {reason} state ({session_state}). "
                f"Gracefully transitioning to a new session on branch '{cycle_manifest.branch_name or 'base'}'."
            )
            return None

        if session_state == "IN_PROGRESS":
            console.print(
                "[yellow]Jules is currently working. Waiting for completion before sending new feedback...[/yellow]"
            )
            await self.jules.wait_for_completion(cycle_manifest.jules_session_id)
            session_state = "COMPLETED"

        is_cold_start = state.status in {FlowStatus.START, None}
        is_post_refactor = state.status == FlowStatus.POST_AUDIT_REFACTOR

        if is_cold_start:
            action_label = "cold start resume"
        elif is_final_fix:
            action_label = "final fix"
        elif is_post_refactor:
            action_label = "final polish"
        else:
            action_label = "feedback retry"

        console.print(
            f"[dim]Reusing session ({session_state}) for {action_label}: {cycle_manifest.jules_session_id}[/dim]"
        )

        # For Post-Audit Refactor, we send the instruction as a message (without wrapping in audit feedback title)
        if state.status == FlowStatus.POST_AUDIT_REFACTOR:
            return await self._send_audit_feedback_to_session(
                cycle_manifest.jules_session_id,
                self._build_instruction(state.cycle_id, None, state, cycle_manifest),
                state=state,
                wrap=False,
            )

        # Build feedback payload
        feedback_payload = ""
        last_audit = state.audit_result

        if state.status == FlowStatus.TDD_FAILED and state.error:
            feedback_payload = str(state.error)
        elif last_audit and last_audit.feedback:
            feedback_payload = (
                "\n".join(last_audit.feedback)
                if isinstance(last_audit.feedback, list)
                else str(last_audit.feedback)
            )

        # Fallback for final_fix if feedback is missing from state
        if not feedback_payload and is_final_fix:
            feedback_payload = "Final Auditor budget reached. Please fix the code one last time and prepare for merging."

        if not feedback_payload:
            if is_cold_start:
                # If we're resuming a cold start, we don't send feedback.
                # The caller should handle retrieving the existing session result.
                return None
            if not is_post_refactor:
                return None

        if cycle_manifest.jules_session_id is not None:
            return await self._send_audit_feedback_to_session(
                cycle_manifest.jules_session_id, feedback_payload, state=state
            )
        return None

    async def _run_jules_session(
        self,
        session_req_id: str,
        instruction: str,
        target_files: list[str],
        context_files: list[str],
        cycle_id: str,
        mgr: StateManager,
        branch: str | None = None,
    ) -> tuple[str | None, dict[str, Any]]:
        """Launch a new Jules session.

        This method should be called within workspace_lock.
        """
        if not re.match(settings.SESSION_ID_PATTERN, session_req_id):
            msg = f"Invalid session_req_id format: {session_req_id}"
            raise ValueError(msg)

        result = await self.jules.run_session(
            session_id=session_req_id,
            prompt=instruction,
            target_files=target_files,
            context_files=context_files,
            require_plan_approval=False,
            branch=branch,
        )

        jules_session_name: str | None = result.get("session_name")

        if jules_session_name:
            mgr.update_cycle_state(
                cycle_id, jules_session_id=jules_session_name, status="in_progress"
            )

        return jules_session_name, result

    async def run_critic_phase(
        self, state: CycleState, cycle_id: str, jules_session_name: str, is_final: bool = False
    ) -> dict[str, Any] | None:
        """Send CODER_CRITIC_INSTRUCTION to Jules and wait for the revised PR.

        Returns the updated result dict, or None if the phase should be skipped.
        """
        if is_final:
            console.print(
                "[bold cyan]Final Refactoring PR created. "
                "Invoking Final Coder Critic for self-reflection before completion...[/bold cyan]"
            )
            template_file = settings.template_files.final_coder_critic_instruction
        else:
            console.print(
                "[bold cyan]Initial Coder PR created. "
                "Invoking Coder Critic for self-reflection before Auditor review...[/bold cyan]"
            )
            template_file = settings.template_files.coder_critic_instruction

        try:
            critic_instruction = settings.get_prompt_content(template_file)
            if not critic_instruction:
                console.print("[red]Failed to load Coder Critic instruction template.[/red]")
                console.print(
                    "[yellow]Warning: Coder Critic template missing, skipping self-reflection...[/yellow]"
                )
                return None

            critic_instruction = critic_instruction.replace("{{cycle_id}}", str(cycle_id))

            session_url = self.jules._get_session_url(jules_session_name)
            await self.jules._send_message(session_url, critic_instruction)

            console.print("[dim]Waiting for Coder Critic to finish review and push fixes...[/dim]")

            result = dict(await self.jules.wait_for_completion(jules_session_name))
            if result and (result.get("status") == "success" or result.get("pr_url")):
                branch_val = result.get("branch_name")
                if branch_val and state.last_processed_commit:
                    current_commit = await self.jules.get_latest_branch_commit(branch_val)
                    if current_commit == state.last_processed_commit:
                        # In critic phase, it's possible no changes were needed.
                        # We don't force a push here, but we update the state to the current commit.
                        logger.info(f"[CRITIC] No new commits in critic phase on {branch_val}. This is acceptable.")
                
                # Always capture the latest state after the phase completes
                await self._update_last_processed_commit(state, result.get("branch_name"))
        except Exception as e:
            console.print(f"[yellow]Warning: Coder Critic phase error, proceeding: {e}[/yellow]")
            return None
        else:
            return result

    async def _update_last_processed_commit(
        self, state: CycleState, branch_name: str | None
    ) -> None:
        """Capture the current commit hash of the branch and update state."""
        if not branch_name:
            return
        commit = await self.jules.get_latest_branch_commit(branch_name)
        if commit and commit != "unknown":
            logger.info(f"Captured last_processed_commit for {branch_name}: {commit}")
            state.last_processed_commit = commit

    async def _send_audit_feedback_to_session(
        self, session_id: str, feedback: str, state: CycleState, wrap: bool = True
    ) -> dict[str, Any] | None:
        """Send audit feedback to existing Jules session and wait for new PR.

        Returns result dict if successful, None if should create new session.
        """
        label = "Audit Feedback" if wrap else "Instruction"
        console.print(
            f"[bold yellow]Sending {label} to existing Jules session: {session_id}[/bold yellow]"
        )
        try:
            if wrap:
                feedback_template = settings.get_prompt_content(
                    settings.template_files.audit_feedback_message
                )
                if not feedback_template:
                    feedback_template = "{{feedback}}"
                feedback_msg = feedback_template.replace("{{feedback}}", feedback)
            else:
                feedback_msg = feedback

            # Continue session sends the message and waits for completion via LangGraph
            result = await self.jules.continue_session(session_id, feedback_msg)
            if result and (result.get("status") == "success" or result.get("pr_url")):
                branch_val = result.get("branch_name")
                if branch_val and state.last_processed_commit:
                    current_commit = await self.jules.get_latest_branch_commit(branch_val)
                    if current_commit == state.last_processed_commit:
                        # If it's a refactoring phase (wrap=False), we can be more lenient
                        # as Jules might legitimately decide no changes are needed.
                        if not wrap:
                            console.print("[bold green]Jules confirms no additional refactoring needed. Proceeding to final review.[/bold green]")
                            return dict(result)

                        console.print(
                            f"[yellow]Warning: Jules completed the turn but no new commits were found on {branch_val}.[/yellow]"
                        )
                        console.print("[cyan]Sending fallback request to Jules to push commits...[/cyan]")
                        push_msg = (
                            "You marked the task as completed, but you did not push any new commits. "
                            "You MUST apply the code changes, run `git commit`, and `git push` before completing your turn. "
                            "Please do this now."
                        )
                        result = await self.jules.continue_session(session_id, push_msg)
                        if result and (result.get("status") == "success" or result.get("pr_url")):
                            current_commit = await self.jules.get_latest_branch_commit(branch_val)
                            if current_commit == state.last_processed_commit:
                                # Still no push after nudge? For audit rejections, this is a failure.
                                raise Exception("Jules refused to push new commits after audit rejection fallback.")
                return dict(result)

            console.print(
                "[yellow]Jules session finished without new PR. Creating new session...[/yellow]"
            )
        except Exception as e:
            logger.exception(f"Failed to send audit feedback to existing session {session_id}")
            console.print(
                f"[yellow]Session continuity failed: {e}. Creating new session...[/yellow]"
            )
        else:
            return None
        return None

    # ------------------------------------------------------------------ #
    #  Utility helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _handle_session_failure(
        self, cycle_manifest: Any, cycle_id: str, error_msg: str, mgr: StateManager
    ) -> dict[str, Any]:
        """Handle session failures and manage restart counting."""
        if cycle_manifest:
            restart_count = cycle_manifest.session_restart_count
            # Force at least 4 retries even if the persisted manifest says lower (e.g. 2)
            max_restarts = max(cycle_manifest.max_session_restarts, 4)

            if restart_count < max_restarts:
                new_restart_count = restart_count + 1

                # Add jittered exponential backoff for transient failures:
                # - FAILED_PRECONDITION (400): Jules API concurrency limit
                # - Network errors: DNS failure, server disconnect, etc.
                _is_precondition = "400" in error_msg or "FAILED_PRECONDITION" in error_msg
                _is_network_error = any(
                    kw in error_msg.lower()
                    for kw in (
                        "errno",
                        "disconnected",
                        "name resolution",
                        "network request failed",
                        "timed out",
                        "silent agent",
                        "timeout",
                    )
                )
                if _is_precondition or _is_network_error:
                    import random

                    backoff = (2**new_restart_count) + random.SystemRandom().uniform(0.5, 2.0)
                    label = (
                        "precondition failure" if _is_precondition else "transient network error"
                    )
                    console.print(
                        f"[yellow]Jules API reported {label}. Backing off for {backoff:.1f}s...[/yellow]"
                    )
                    await asyncio.sleep(backoff)

                console.print(
                    f"[yellow]Restarting session (attempt {new_restart_count}/{max_restarts + 1})...[/yellow]"
                )
                mgr.update_cycle_state(
                    cycle_id,
                    jules_session_id=None,
                    session_restart_count=new_restart_count,
                    last_error=error_msg,
                )
                return {
                    "status": FlowStatus.CODER_RETRY,
                }

            console.print(
                f"[red]Max session restarts ({max_restarts}) reached. Cycle {cycle_id} failed.[/red]"
            )
            console.print(f"[red]Final error: {error_msg}[/red]")

        return {"status": FlowStatus.FAILED, "error": error_msg}

    def _build_feedback_injection(self, feedback: str, pr_url: str | None) -> str:
        """Build feedback injection block from template."""
        template = str(
            settings.get_prompt_content(settings.template_files.audit_feedback_injection)
        )
        if not template:
            template = "{{feedback}}"
        result = template.replace("{{feedback}}", feedback)
        if pr_url:
            result = str(
                re.sub(
                    r"\{\{#pr_url\}\}\s*Previous PR: \{\{pr_url\}\}\s*\{\{/pr_url\}\}",
                    f"Previous PR: {pr_url}",
                    result,
                    flags=re.DOTALL,
                )
            )
        else:
            result = str(re.sub(r"\{\{#pr_url\}\}.*?\{\{/pr_url\}\}", "", result, flags=re.DOTALL))
        return result.strip()
