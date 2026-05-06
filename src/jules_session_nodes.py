"""LangGraph nodes for Jules session management."""

import asyncio
from typing import Any

import httpx
from rich.console import Console

from src.jules_session_state import JulesSessionState, SessionStatus
from src.utils import logger

console = Console()


class JulesSessionNodes:
    """Collection of LangGraph nodes for Jules session management."""

    def __init__(self, jules_client: Any) -> None:
        """Initialize with reference to JulesClient for API calls."""
        self.client = jules_client

    def _compute_diff(
        self, original: JulesSessionState, current: JulesSessionState
    ) -> dict[str, Any]:
        """Compute dictionary of changed fields for LangGraph checkpointer."""
        updates = {}
        for field in current.model_fields:
            old_val = getattr(original, field)
            new_val = getattr(current, field)
            if old_val != new_val:
                updates[field] = new_val
        return updates

    async def monitor_session(self, _state_in: JulesSessionState) -> dict[str, Any]:  # noqa: C901, PLR0915, PLR0911
        """Monitor Jules session and detect state changes with batched polling."""
        from src.config import settings

        state = _state_in.model_copy(deep=True)

        # Batch polling loop to reduce graph steps
        # Poll for (monitor_batch_size * monitor_poll_interval_seconds) seconds per LangGraph invocation
        batch_size = settings.jules.monitor_batch_size
        batch_size = settings.jules.monitor_batch_size
        poll_interval = settings.jules.monitor_poll_interval_seconds

        now = asyncio.get_running_loop().time

        # Initialise last_jules_state_change_time on first call
        if state.last_jules_state_change_time == 0.0:
            state.last_jules_state_change_time = now()

        for _ in range(batch_size):
            # Check timeout
            elapsed = now() - state.start_time
            if elapsed > state.timeout_seconds:
                logger.warning(f"Session timeout after {elapsed}s")
                state.status = SessionStatus.TIMEOUT
                state.error = f"Timed out after {elapsed}s"
                return self._compute_diff(_state_in, state)

            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    # Fetch session state
                    response = await client.get(
                        state.session_url, headers=self.client._get_headers()
                    )
                    response.raise_for_status()
                    data = response.json()

                    new_jules_state = data.get("state")

                    # --- NEW: Activity-Aware Watchdog Update ---
                    # We fetch activities to check for progress even if the state string (e.g. IN_PROGRESS) hasn't changed.
                    activities = await self.client.list_activities(state.session_url)
                    activity_count = len(activities)

                    if (
                        state.jules_state != new_jules_state
                        or activity_count > state.last_activity_count
                    ):
                        if state.jules_state != new_jules_state:
                            state.previous_jules_state = state.jules_state

                        state.last_jules_state_change_time = (
                            now()
                        )  # reset stale clock on state change OR activity
                        logger.debug(
                            f"Watchdog reset: State={new_jules_state}, ActivityCount={activity_count}"
                        )

                    state.jules_state = new_jules_state
                    state.raw_data = data
                    state.last_activity_count = activity_count

                    # Only emit INFO when state changes; repeated same-state polls are demoted to DEBUG
                    if new_jules_state != _state_in.jules_state:
                        logger.info(
                            f"Jules session state changed: {_state_in.jules_state} → {new_jules_state}"
                        )
                    else:
                        logger.debug(f"Jules session state (unchanged): {new_jules_state}")

                    # ── Hard Activity Watchdog (Silent Agent Prevention) ──────────
                    # States that represent Jules is "silent" but still ownership of the turn.
                    # Explicitly include PAUSED and AWAITING_PLAN_APPROVAL to avoid 2-hour stalls.
                    stale_working_states = {
                        "IN_PROGRESS",
                        "PLANNING",
                        "QUEUED",
                        "PAUSED",
                        "AWAITING_PLAN_APPROVAL",
                    }
                    all_stale_states = stale_working_states | {"AWAITING_USER_FEEDBACK"}

                    if state.jules_state in all_stale_states:
                        stale_seconds = now() - state.last_jules_state_change_time
                        nudge_interval = settings.jules.stale_session_timeout_seconds
                        max_nudges = settings.jules.max_stale_nudges

                        if stale_seconds >= nudge_interval:
                            if state.stale_nudge_count < max_nudges:
                                # Send a nudge instead of failing
                                state.stale_nudge_count += 1
                                logger.warning(
                                    f"Silent Agent detected ({stale_seconds:.0f}s). "
                                    f"Sending nudge #{state.stale_nudge_count}/{max_nudges}..."
                                )
                                nudge_msg = (
                                    "[System Status Check] You have been in progress for 30 minutes. "
                                    "Please provide a very brief status update if you are still working, "
                                    "or continue to completion if you are almost finished."
                                )
                                try:
                                    await self.client._send_message(state.session_url, nudge_msg)
                                    # Reset state change time to allow another interval for the next nudge
                                    state.last_jules_state_change_time = now()
                                except Exception as e:
                                    logger.error(f"Failed to send nudge message: {e}")
                                    # 404 on sendMessage usually means Jules already completed the session
                                    # or the session was auto-deleted after it finished. Re-check state
                                    # before giving up so we don't lose a successfully completed session.
                                    try:
                                        refreshed_state = await self.client.get_session_state(
                                            state.session_url
                                        )
                                        logger.info(
                                            f"Nudge failed — re-checking Jules state: {refreshed_state}"
                                        )
                                        if refreshed_state in ("COMPLETED", "FAILED"):
                                            # Jules finished — update state and let the normal
                                            # completion / failure path handle the result.
                                            state.jules_state = refreshed_state
                                            state.last_jules_state_change_time = now()
                                            logger.info(
                                                f"Session already {refreshed_state}. Continuing to validation."
                                            )
                                            break  # exit batch loop → normal routing will take over
                                        # Otherwise truly stuck — escalate as before
                                    except Exception as refresh_err:
                                        logger.warning(
                                            f"Could not re-check session state: {refresh_err}"
                                        )
                                    state.status = SessionStatus.TIMEOUT
                                    state.error = f"Silent Agent timeout and nudge failed: {e}"
                                    return self._compute_diff(_state_in, state)
                            else:
                                msg = (
                                    f"Silent Agent Persistent: Jules has been in {state.jules_state} "
                                    f"for over {max_nudges * nudge_interval / 60:.0f} minutes with no state changes. "
                                    "Exhausted all nudges. Escalating to TIMEOUT."
                                )
                                logger.error(msg)
                                state.status = SessionStatus.TIMEOUT
                                state.error = msg
                                return self._compute_diff(_state_in, state)
                    # ── end activity watchdog ───────────────────────────────────────
                    # ── end stale detection ─────────────────────────────────────────

                    # Check for failure
                    if state.jules_state == "FAILED":
                        # Resilience: Check if a PR was created first (common in COMPLETED -> FAILED transients)
                        pr_found = any(
                            "pullRequest" in output for output in data.get("outputs", [])
                        )

                        # NEW: Also scan activities for a PR (sometimes it's only in progressUpdated events)
                        if not pr_found:
                            logger.info(
                                f"PR not in outputs for FAILED session {state.session_name}. Scanning activities..."
                            )
                            try:
                                activities = await self.client.list_activities(state.session_url)
                                for act in activities:
                                    # Check progressUpdated or other activities that might wrap outputs
                                    # (Jules API: progressUpdated.outputs[].pullRequest)
                                    for key in ["progressUpdated", "sessionCompleted"]:
                                        if key in act:
                                            outputs = act[key].get("outputs", [])
                                            if any("pullRequest" in o for o in outputs):
                                                pr_found = True
                                                logger.info(
                                                    f"Found PR in {key} activity for FAILED session!"
                                                )
                                                break
                                    if pr_found:
                                        break
                            except Exception as e:
                                logger.debug(
                                    f"Failed to scan activities for PR in FAILED session: {e}"
                                )

                        if pr_found:
                            logger.info(
                                f"Session {state.session_name} in FAILED state, but PR detected. Proceeding to validation."
                            )
                            state.status = SessionStatus.CHECKING_PR
                            return self._compute_diff(_state_in, state)

                        error_msg = "Unknown error"
                        # Strategy 1: Check session outputs (fastest)
                        for output_item in data.get("outputs", []):
                            reason = output_item.get("sessionFailed", {}).get("reason")
                            if reason:
                                error_msg = reason
                                break

                        if error_msg == "Unknown error":
                            # Strategy 2: Fetch activities
                            logger.info(
                                f"Reason missing from outputs for {state.session_name}. Fetching activities..."
                            )
                            try:
                                activities = await self.client.list_activities(state.session_url)
                                for act in activities:
                                    if "sessionFailed" in act:
                                        error_msg = act["sessionFailed"].get(
                                            "reason", "Unknown error"
                                        )
                                        break
                            except Exception as e:
                                logger.warning(f"Failed to fetch activities: {e}")

                        # Strategy 3: Last-ditch recovery nudge
                        if not state.recovery_nudge_sent:
                            logger.warning(
                                f"Session {state.session_name} failed. Sending recovery nudge..."
                            )
                            recovery_msg = (
                                "The session failed unexpectedly. Please check your progress and continue. "
                                "If you were about to create a PR, please do so now."
                            )
                            try:
                                await self.client._send_message(state.session_url, recovery_msg)
                                state.recovery_nudge_sent = True
                                state.last_jules_state_change_time = now()
                                logger.info("Recovery nudge sent. Waiting for response...")
                                continue
                            except Exception as e:
                                logger.warning(f"Failed to send recovery nudge: {e}")

                        logger.error(f"Jules Session FAILED. Reason: {error_msg}")
                        state.status = SessionStatus.FAILED
                        state.error = f"Jules Session Failed: {error_msg}"
                        return self._compute_diff(_state_in, state)

                    # Process inquiries (questions and plan approvals)
                    await self._process_inquiries_in_monitor(state, client)

                    # CRITICAL FIX: If an inquiry was detected, return immediately to handle it.
                    # Do NOT let "COMPLETED" status overwrite a pending question.
                    if state.status == SessionStatus.INQUIRY_DETECTED:
                        return self._compute_diff(_state_in, state)

                    # Reset validation flag if we are back in working states
                    # terminal states: COMPLETED, FAILED
                    if state.jules_state not in ["COMPLETED", "FAILED"]:
                        state.completion_validated = False
                        state.expect_new_work = False  # Work has started!

                    # Check for completion
                    if state.jules_state == "COMPLETED" and not state.completion_validated:
                        # If we just sent a message and Jules is still in COMPLETED,
                        # we must wait for it to actually start working.
                        if state.expect_new_work:
                            stale_seconds = now() - state.last_jules_state_change_time
                            # SAFEGUARD: If we've been waiting for a transition out of COMPLETED for too long,
                            # it means Jules likely ignored our last message or finished instantly.
                            if stale_seconds >= 300:  # 5 minutes
                                logger.warning(
                                    f"Session stuck in COMPLETED for {stale_seconds:.0f}s despite expect_new_work. "
                                    "Resetting flag and proceeding to validation."
                                )
                                state.expect_new_work = False
                                state.status = SessionStatus.VALIDATING_COMPLETION
                                return self._compute_diff(_state_in, state)

                            logger.info(
                                f"Session still in stale COMPLETED state ({stale_seconds:.0f}s). Waiting for work to start..."
                            )
                        else:
                            state.status = SessionStatus.VALIDATING_COMPLETION
                            return self._compute_diff(_state_in, state)

                    # Handle manual user input
                    await self.client._handle_manual_input(state.session_url)

            except Exception as e:
                logger.warning(f"Monitor loop error (transient): {e}")

            # Continue monitoring loop
            # We use a short sleep here because we are inside the batch loop
            # state.poll_interval is typically long (120s), but for batching we want shorter interval (5s)
            # We ignore state.poll_interval here and use fixed 5s for responsiveness
            await self.client._sleep(poll_interval)

        return self._compute_diff(_state_in, state)

    async def _process_inquiries_in_monitor(
        self, state: JulesSessionState, client: httpx.AsyncClient
    ) -> None:
        """Check for and process inquiries during monitoring.

        Only acts when Jules is explicitly waiting for user input:
        - AWAITING_PLAN_APPROVAL  -> check for plan to approve
        - AWAITING_USER_FEEDBACK  -> check for inquiryAsked activity
        Any other state means Jules is working; we must not interrupt.
        """
        # Plan approval: only when Jules is waiting for it
        if state.require_plan_approval and state.jules_state == "AWAITING_PLAN_APPROVAL":
            await self.client.inquiry_handler.handle_plan_approval(
                client,
                state.session_url,
                state.processed_activity_ids,
                [state.plan_rejection_count],
                state.max_plan_rejections,
            )

        # Regular inquiry: forward jules_state so the handler applies the state-guard
        inquiry = await self.client.inquiry_handler.check_for_inquiry(
            client, state.session_url, state.processed_activity_ids, jules_state=state.jules_state
        )
        if inquiry:
            question, act_id = inquiry
            if act_id and act_id not in state.processed_activity_ids:
                state.current_inquiry = question
                state.current_inquiry_id = act_id
                state.status = SessionStatus.INQUIRY_DETECTED

    async def _update_activity_count(
        self, state: JulesSessionState, client: httpx.AsyncClient
    ) -> None:
        """Update activity count for progress tracking."""
        try:
            activities = await self.client.list_activities(state.session_url)
            if len(activities) > state.last_activity_count:
                console.print(f"[dim]Activity Count: {len(activities)}[/dim]")
                state.last_activity_count = len(activities)
        except Exception:  # noqa: S110
            pass

    async def answer_inquiry(self, _state_in: JulesSessionState) -> dict[str, Any]:
        """Answer Jules' inquiry using Manager Agent."""
        state = _state_in.model_copy(deep=True)

        if not state.current_inquiry or not state.current_inquiry_id:
            state.status = SessionStatus.MONITORING
            return self._compute_diff(_state_in, state)

        console.print(
            f"\n[bold magenta]Jules Question Detected:[/bold magenta] {state.current_inquiry}"
        )
        console.print("[dim]Consulting Manager Agent with full context...[/dim]")

        try:
            # Build comprehensive context
            enhanced_context = await self.client.context_builder.build_question_context(
                state.current_inquiry
            )
            console.print(f"[dim]Context size: {len(enhanced_context)} chars[/dim]")

            # Get Manager Agent response with retries
            max_retries = 3
            reply_text = ""
            for attempt in range(max_retries):
                try:
                    mgr_response = await self.client.manager_agent.run(enhanced_context)
                    reply_text = mgr_response.output
                    break
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    logger.warning(f"Manager Agent attempt {attempt + 1} failed: {e}. Retrying...")
                    await asyncio.sleep(2**attempt)

            from src.config import settings

            followup = settings.get_prompt_content(
                "MANAGER_INQUIRY_FOLLOWUP.md",
                default="(System Note: If task complete/blocker resolved, proceed to create PR. Do not wait.)",
            )
            reply_text += f"\n\n{followup}"

            console.print(f"[bold cyan]Manager Agent Reply:[/bold cyan] {reply_text}")
            await self.client._send_message(state.session_url, reply_text)
            state.processed_activity_ids.add(state.current_inquiry_id)

            # Clear inquiry
            state.current_inquiry = None
            state.current_inquiry_id = None

            await self.client._sleep(5)

        except Exception as e:
            logger.error(f"Manager Agent failed: {e}")
            from src.config import settings

            fallback_template = settings.get_prompt_content(
                "MANAGER_INQUIRY_FALLBACK.md",
                default="I encountered an error processing your question. Original question: {{question}}",
            )
            fallback_msg = fallback_template.replace("{{question}}", state.current_inquiry or "")
            await self.client._send_message(state.session_url, fallback_msg)
            if state.current_inquiry_id is not None:
                state.processed_activity_ids.add(state.current_inquiry_id)

        state.status = SessionStatus.MONITORING
        return self._compute_diff(_state_in, state)

    async def validate_completion(self, _state_in: JulesSessionState) -> dict[str, Any]:  # noqa: C901
        """Validate if COMPLETED state is genuine or if work is still ongoing."""
        state = _state_in.model_copy(deep=True)

        try:
            # Fetch recent activities
            activities = await self.client.list_activities(state.session_url)

            # First, check for sessionCompleted activity (most reliable indicator)
            has_session_completed = False
            stale_completion_detected = False

            for activity in activities:
                if "sessionCompleted" in activity:
                    # Check if this is a stale (already processed) event
                    act_id = activity.get("name", activity.get("id"))
                    if act_id and act_id in state.processed_completion_ids:
                        stale_completion_detected = True
                        continue

                    if act_id:
                        state.processed_completion_ids.add(act_id)

                    has_session_completed = True
                    logger.info("Found sessionCompleted activity - session is genuinely complete")
                    break

            # If sessionCompleted exists (and is new), it's genuinely complete
            if has_session_completed:
                state.completion_validated = True
                state.status = SessionStatus.CHECKING_PR
                return self._compute_diff(_state_in, state)

            # If we found a STALE completion, we must NOT fall back to checking PRs
            # because we are likely in a feedback loop where state hasn't updated yet.
            if stale_completion_detected:
                # Force wait if we explicitly expect new work
                if state.expect_new_work:
                    logger.info(
                        "Stale completion detected while expecting new work. Returning to monitor."
                    )
                    state.status = SessionStatus.MONITORING
                    return self._compute_diff(_state_in, state)

                # Allow proceed if we observed a valid IN_PROGRESS -> COMPLETED transition
                # This handles cases where Jules re-completes but doesn't emit a new completion event
                if state.previous_jules_state == "IN_PROGRESS":
                    logger.info(
                        "Stale completion detected, BUT valid IN_PROGRESS->COMPLETED transition observed. Treating as complete."
                    )
                # If we are not expecting new work (e.g. cold start resume),
                # a stale completion is still a valid state to proceed.
                elif not state.expect_new_work:
                    logger.info(
                        "Stale completion detected during resume/non-interactive check. Proceeding."
                    )
                else:
                    logger.info(
                        "Stale completion detected (ignored). Waiting for new Agent activity..."
                    )
                    state.status = SessionStatus.MONITORING
                    return self._compute_diff(_state_in, state)

            # Logic removed: Checking for ongoing work indicators via keywords caused infinite loops.

            # NEW FIX: If sessionCompleted is missing, check for distress/objections in the last message.
            # This prevents auditing when Jules is complaining (e.g. "feedback inconsistent") but ends session.
            if not has_session_completed:
                distress_state = await self._check_for_distress_in_messages(state)
                if distress_state:
                    return self._compute_diff(_state_in, distress_state)

            # If Jules API says COMPLETED, we should trust it and proceed to PR check.
            # If PR is missing, check_pr will handle it by requesting PR creation.

        except Exception as e:
            logger.warning(f"Failed to validate completion: {e}")

        # If no sessionCompleted found and no ongoing work, verify if output exists
        logger.info(
            "Jules reported turn as COMPLETED. Verifying output fidelity (Checking for PR)..."
        )
        state.completion_validated = True
        state.status = SessionStatus.CHECKING_PR
        return self._compute_diff(_state_in, state)

    def _extract_pr_from_outputs(
        self, outputs: list[dict[str, Any]]
    ) -> tuple[str | None, str | None]:
        """Helper to find PR info in a list of session outputs."""
        for output in outputs:
            if "pullRequest" in output:
                pr = output["pullRequest"]
                pr_url = pr.get("url")
                if pr_url:
                    return pr_url, pr.get("headRef")
        return None, None

    async def _scan_activities_for_pr(self, session_url: str) -> tuple[str | None, str | None]:
        """Deep scan activities for PR URL (fallback)."""
        try:
            activities = await self.client.list_activities(session_url)
            for act in activities:
                for key in ["progressUpdated", "sessionCompleted", "agentMessaged"]:
                    if key not in act:
                        continue
                    if key == "agentMessaged":
                        msg = act["agentMessaged"].get("agentMessage", "")
                        if "github.com/" in msg and "/pull/" in msg:
                            import re

                            urls = re.findall(r"https://github\.com/[\w\-/]+/pull/\d+", msg)
                            if urls:
                                return urls[0], None
                    else:
                        outputs = act[key].get("outputs", [])
                        pr_url, head_ref = self._extract_pr_from_outputs(outputs)
                        if pr_url:
                            return pr_url, head_ref
        except Exception as e:
            logger.warning(f"Activity scan failed: {e}")
        return None, None

    async def check_pr(self, _state_in: JulesSessionState) -> dict[str, Any]:
        """Check for PR in session outputs."""
        state = _state_in.model_copy(deep=True)
        if not state.raw_data:
            state.status = SessionStatus.REQUESTING_PR_CREATION
            return self._compute_diff(_state_in, state)

        # 1. Check existing outputs
        pr_url, branch = self._extract_pr_from_outputs(state.raw_data.get("outputs", []))
        if pr_url:
            console.print(f"\n[bold green]PR Created: {pr_url}[/bold green]")
            state.pr_url, state.branch_name = pr_url, branch
            state.status = SessionStatus.SUCCESS
            return self._compute_diff(_state_in, state)

        # 2. Refresh session data
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                state.session_url, headers=self.client._get_headers(), timeout=10.0
            )
            if resp.status_code == httpx.codes.OK:
                fresh_data = resp.json()
                pr_url, branch = self._extract_pr_from_outputs(fresh_data.get("outputs", []))
                if pr_url:
                    console.print(f"\n[bold green]PR Created: {pr_url}[/bold green]")
                    state.pr_url, state.branch_name = pr_url, branch
                    state.raw_data, state.status = fresh_data, SessionStatus.SUCCESS
                    return self._compute_diff(_state_in, state)

        # 3. Fallback: Deep scan activities
        logger.debug(f"PR not in outputs for {state.session_name}. Scanning activities...")
        pr_url, branch = await self._scan_activities_for_pr(state.session_url)
        if pr_url:
            state.pr_url, state.branch_name = pr_url, branch
            state.status = SessionStatus.SUCCESS
            return self._compute_diff(_state_in, state)

        console.print("[yellow]Session Completed but NO PR found.[/yellow]")
        state.status = SessionStatus.REQUESTING_PR_CREATION
        return self._compute_diff(_state_in, state)

    async def _check_for_distress_in_messages(
        self, state: JulesSessionState
    ) -> JulesSessionState | None:
        """Checks the latest agentMessaged activity for distress signals/objections.

        Jules API has no /messages endpoint. The correct way to read agent messages
        is via agentMessaged activities from GET /sessions/{session}/activities.
        """
        try:
            activities = await self.client.list_activities(state.session_url)

            # Find the most recent agentMessaged activity (originator=agent)
            last_agent_msg: dict[str, Any] | None = None
            for act in activities:
                if "agentMessaged" in act and act.get("originator", "") == "agent":
                    last_agent_msg = act

            if not last_agent_msg:
                return None

            content = last_agent_msg.get("agentMessaged", {}).get("agentMessage", "").lower()
            msg_id = last_agent_msg.get("name") or str(hash(content))

            if msg_id in state.processed_activity_ids:
                return None

            from src.config import settings

            distress_keywords = settings.jules.distress_keywords
            if any(k in content for k in distress_keywords):
                logger.warning(
                    "Detected distress/objection in latest agentMessaged activity. Treating as inquiry."
                )
                state.current_inquiry = last_agent_msg.get("agentMessaged", {}).get("agentMessage")
                state.current_inquiry_id = msg_id
                state.status = SessionStatus.INQUIRY_DETECTED
                return state
        except Exception as e:
            logger.warning(f"Failed to check agentMessaged activities for distress: {e}")
        return None

    async def request_pr_creation(self, _state_in: JulesSessionState) -> dict[str, Any]:
        """Request Jules to create a PR manually (fallback for when AUTO_CREATE_PR failed).

        With AUTO_CREATE_PR enabled, Jules should create the PR automatically.
        This node is only reached when COMPLETED state has no PR in session outputs.
        We do one final re-fetch before sending any message, in case raw_data was stale.
        """
        state = _state_in.model_copy(deep=True)

        # Final safety check: re-fetch session outputs before sending any message.
        # AUTO_CREATE_PR mode should create the PR automatically. If we reach this node
        # it means check_pr didn't find a PR, but the data might have been stale.
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(
                    state.session_url, headers=self.client._get_headers(), timeout=10.0
                )
                if resp.status_code == httpx.codes.OK:
                    fresh_data = resp.json()
                    for output in fresh_data.get("outputs", []):
                        if "pullRequest" in output:
                            pr_url = output["pullRequest"].get("url")
                            if pr_url:
                                console.print(
                                    f"\n[bold green]PR found on final check: {pr_url}[/bold green]"
                                )
                                logger.info(
                                    f"PR found in final re-fetch, skipping manual PR request: {pr_url}"
                                )
                                state.pr_url = pr_url
                                state.raw_data = fresh_data
                                state.status = SessionStatus.SUCCESS
                                return self._compute_diff(_state_in, state)
        except Exception as e:
            logger.debug(f"Final PR check failed: {e}")

        console.print("\n[bold yellow]--- Verifying Genuine Completion ---[/bold yellow]")
        console.print(
            "[yellow]Jules completed the turn but NO PR was found. Sending fallback request...[/yellow]"
        )
        console.print("[cyan]Requesting Jules to commit and create PR...[/cyan]")

        from src.config import settings

        message = settings.get_template("PR_CREATION_REQUEST.md").read_text()

        await self.client._send_message(state.session_url, message)
        console.print("[dim]Waiting for Jules to create PR...[/dim]")

        state.status = SessionStatus.WAITING_FOR_PR
        state.fallback_elapsed_seconds = 0
        return self._compute_diff(_state_in, state)

    async def wait_for_pr(self, _state_in: JulesSessionState) -> dict[str, Any]:  # noqa: C901
        """Wait for PR creation after manual request, with session state re-validation."""
        state = _state_in.model_copy(deep=True)

        await self.client._sleep(10)
        state.fallback_elapsed_seconds += 10

        # Check timeout
        if state.fallback_elapsed_seconds >= state.fallback_max_wait:
            logger.warning(f"Timeout ({state.fallback_max_wait}s) waiting for Jules to create PR")
            state.status = SessionStatus.TIMEOUT
            state.error = f"Timeout waiting for PR after {state.fallback_max_wait}s"
            return self._compute_diff(_state_in, state)

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # Re-check session state (Jules might have gone back to work)
                session_resp = await client.get(
                    state.session_url, headers=self.client._get_headers()
                )
                if session_resp.status_code == httpx.codes.OK:
                    current_state = session_resp.json().get("state")
                    # Return to monitoring for any active/working state
                    # (official Jules API non-terminal states)
                    ACTIVE_STATES = {
                        "IN_PROGRESS",
                        "QUEUED",
                        "PLANNING",
                        "AWAITING_PLAN_APPROVAL",
                        "AWAITING_USER_FEEDBACK",
                        "PAUSED",
                    }
                    if current_state in ACTIVE_STATES:
                        logger.info(
                            f"Session returned to {current_state} during PR wait. Returning to monitoring."
                        )
                        state.status = SessionStatus.MONITORING
                        state.jules_state = current_state
                        return self._compute_diff(_state_in, state)

                # Re-fetch session to check for PR in outputs (Jules API: PR is in session outputs, not activities)
                session_resp = await client.get(
                    state.session_url, headers=self.client._get_headers(), timeout=10.0
                )
                if session_resp.status_code == httpx.codes.OK:
                    fresh_data = session_resp.json()
                    for output in fresh_data.get("outputs", []):
                        if "pullRequest" in output:
                            pr_url = output["pullRequest"].get("url")
                            if pr_url:
                                console.print(f"[bold green]PR Created: {pr_url}[/bold green]")
                                state.pr_url = pr_url
                                state.status = SessionStatus.SUCCESS
                                return self._compute_diff(_state_in, state)

                # Log new agentMessaged activities (the only activity type with human-readable text)
                try:
                    activities = await self.client.list_activities(state.session_url)
                    for activity in activities:
                        act_id = activity.get("name", activity.get("id"))
                        if act_id and act_id not in state.processed_fallback_ids:
                            msg = self.client._extract_activity_message(activity)
                            if msg:
                                console.print(f"[dim]Jules: {msg}[/dim]")
                            state.processed_fallback_ids.add(act_id)
                except Exception as e:
                    logger.debug(f"Failed to fetch activities during PR wait: {e}")

        except Exception as e:
            logger.debug(f"Error checking for PR/activities: {e}")

        # Progress update
        if state.fallback_elapsed_seconds % 30 == 0:
            console.print(
                f"[dim]Still waiting for PR... ({state.fallback_elapsed_seconds}/{state.fallback_max_wait}s elapsed)[/dim]"
            )

        # Continue waiting
        return self._compute_diff(_state_in, state)
