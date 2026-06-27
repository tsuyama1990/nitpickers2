"""Jules API client wrapping jules-agent-sdk-python.

Replaces the previous custom httpx-based implementation with a thin wrapper
around the AsyncJulesClient from the community-maintained SDK.
"""

import asyncio
import json
import os
from typing import Any

from jules_agent_sdk import AsyncJulesClient
from jules_agent_sdk.models import SessionState
from rich.console import Console

from src.config import settings
from src.domain_models import DispatcherConfig, TracingMetadata
from src.services.async_dispatcher import retry_on_429
from src.services.git_ops import GitManager
from src.utils import logger

from .jules.context_builder import JulesContextBuilder
from .jules.git_context import JulesGitContext

console = Console()


# --- Exception Classes ---
class JulesSessionError(Exception):
    pass


class JulesTimeoutError(JulesSessionError):
    pass


class JulesApiError(Exception):
    pass


# --- Service Client Implementation ---
class JulesClient:
    """
    Client for interacting with the Google Cloud Code Agents API (Jules API).

    Wraps ``AsyncJulesClient`` from ``jules-agent-sdk``.
    """

    def __init__(self) -> None:
        self.timeout = settings.jules.timeout_seconds
        self.poll_interval = settings.jules.polling_interval_seconds
        self.git = GitManager()

        api_key = settings.JULES_API_KEY.get_secret_value() or os.getenv("JULES_API_KEY")
        base_url = settings.jules.base_url

        if not api_key and "PYTEST_CURRENT_TEST" not in os.environ:
            errmsg = "Missing JULES_API_KEY or ADC credentials."
            raise JulesSessionError(errmsg)

        self.sdk_client = AsyncJulesClient(
            api_key=api_key or "",
            base_url=base_url,
        )

        self.context_builder = JulesContextBuilder(self.git)
        self.git_context = JulesGitContext(self.git)

    async def list_activities(self, session_id: str) -> list[Any]:
        """List all activities for a session via SDK.

        Args:
            session_id: Session ID (with or without "sessions/" prefix).

        Returns:
            List of Activity objects from the SDK.
        """
        return await self.sdk_client.activities.list_all(session_id)

    async def run_session(
        self,
        session_id: str,
        prompt: str,
        files: list[str] | None = None,
        require_plan_approval: bool = False,
        branch: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Orchestrates the Jules session using the SDK.

        Args:
            session_id: Unique ID for this session (used for tracking, SDK generates its own).
            prompt: The main prompt / instruction for Jules.
            files: List of files to focus on (legacy, merged into prompt).
            require_plan_approval: Whether Jules should pause for plan approval.
            branch: Git branch to use (defaults to current branch).
            **extra: Additional kwargs (title, target_files, context_files).

        Returns:
            Dict with session_name and status.
        """
        owner, repo_name, context_branch = await self.git_context.prepare_git_context(branch=branch)
        branch_to_use = branch or context_branch
        full_prompt = self.context_builder.construct_run_prompt(
            prompt, files, extra.get("target_files"), extra.get("context_files")
        )

        source = f"sources/github/{owner}/{repo_name}"

        title = extra.get("title")

        session = await self.sdk_client.sessions.create(
            prompt=full_prompt,
            source=source,
            starting_branch=branch_to_use,
            title=str(title) if title else None,
            require_plan_approval=require_plan_approval,
        )

        session_name: str = session.name or session.id

        # Rate-limit buffer: Jules API rejects with FAILED_PRECONDITION when too many
        # sessions are created in a short window.
        await asyncio.sleep(10)

        return {"session_name": session_name, "status": "running"}

    async def continue_session(self, session_name: str, prompt: str) -> dict[str, Any]:
        """Continues an existing session by sending a message and waiting for completion.

        Args:
            session_name: Session resource name (e.g. "sessions/abc123").
            prompt: The message / follow-up instruction to send.

        Returns:
            Dict with status, pr_url, session_name.
        """
        logger.info(f"Continuing Session {session_name} with info...")
        try:
            await self._send_message(session_name, prompt)
        except Exception as e:
            logger.error(f"Failed to send message to session {session_name}: {e!r}")
            raise

        logger.info(f"Waiting for Jules to process feedback for {session_name}...")
        result = await self.wait_for_completion(session_name, expect_new_work=True)
        result["session_name"] = session_name
        return result

    async def wait_for_completion(  # noqa: C901, PLR0915
        self,
        session_name: str,
        require_plan_approval: bool = False,
        expect_new_work: bool = False,
    ) -> dict[str, Any]:
        """Wait for Jules session completion using a direct while-loop.

        Args:
            session_name: The session to wait for.
            require_plan_approval: Whether to audit and approve/reject plans.
            expect_new_work: If True, wait for Jules to leave its current terminal
                             state before monitoring (used after sending a message).

        Returns:
            Dict with status, pr_url, branch_name, session_name.

        Raises:
            JulesTimeoutError: On global timeout.
            JulesSessionError: On session failure.
        """
        _tracing_metadata = TracingMetadata(
            session_id=f"jules-{session_name}", execution_type="jules_session"
        )
        _tracing_config = settings.tracing_service.get_run_config(_tracing_metadata)

        console.print(
            f"[bold green]Jules is working... (Session: {session_name})[/bold green]"
        )

        from src.services.plan_auditor import PlanAuditor

        plan_auditor = PlanAuditor()

        try:
            async with asyncio.timeout(self.timeout):
                session_id = self._normalize_session_id(session_name)
                sdk = self.sdk_client
                loop = asyncio.get_running_loop()
                start_time = loop.time()
                timeout = float(self.timeout)
                poll_interval = float(self.poll_interval)

                # Stale (silent agent) detection
                stale_state_time: float = start_time
                stale_nudge_count: int = 0
                max_nudges: int = getattr(settings.jules, "max_stale_nudges", 3)
                nudge_interval: int = getattr(settings.jules, "stale_session_timeout_seconds", 1800)

                # Plan rejection tracking
                rejection_count: int = 0
                max_rejections: int = getattr(settings.jules, "max_plan_rejections", 2)

                # Processed activity IDs to avoid re-processing
                processed_ids: set[str] = set()

                # If expect_new_work, wait for Jules to leave terminal state
                if expect_new_work:
                    terminal_states = {"COMPLETED", "FAILED"}
                    wait_start = loop.time()
                    while True:
                        try:
                            session = await sdk.sessions.get(session_id)
                            current = self._get_state_str(session)
                            if current not in terminal_states:
                                logger.info(
                                    f"Session {session_name} left terminal state → {current}"
                                )
                                break
                        except Exception as e:
                            logger.debug(f"Failed to check session state: {e}")

                        if loop.time() - wait_start > 300:  # 5 min safe guard
                            logger.warning(
                                f"Session {session_name} stuck in terminal state after 300s. "
                                "Proceeding anyway."
                            )
                            break
                        await asyncio.sleep(5)

                # ── Main monitoring loop ──────────────────────────────────────
                while True:
                    # 1. Check global timeout
                    elapsed = loop.time() - start_time
                    if elapsed > timeout:
                        msg = f"Session {session_name} timed out after {timeout:.0f}s"
                        logger.error(msg)
                        raise JulesTimeoutError(msg)

                    # 2. Fetch session state via SDK
                    try:
                        session = await sdk.sessions.get(session_id)
                        jules_state = self._get_state_str(session)
                    except Exception as e:
                        logger.warning(f"Failed to get session state: {e}")
                        await asyncio.sleep(poll_interval)
                        continue

                    logger.debug(f"Session {session_name} state: {jules_state}")

                    # 3. State handling
                    try:
                        if jules_state == "AWAITING_USER_FEEDBACK":
                            handled = await self._handle_user_feedback(
                                session_id, processed_ids, rejection_count, max_rejections,
                                require_plan_approval, plan_auditor,
                            )
                            if handled:
                                stale_state_time = loop.time()
                            continue

                        if jules_state == "AWAITING_PLAN_APPROVAL":
                            await self._handle_plan_approval(
                                session_id, processed_ids, rejection_count, max_rejections,
                                plan_auditor,
                            )
                            stale_state_time = loop.time()
                            continue

                        if jules_state == "COMPLETED":
                            return self._handle_completed(session, session_name)

                        if jules_state == "FAILED":
                            return await self._handle_failed(
                                session_id=session_id,
                                session=session,
                                session_name=session_name,
                                processed_ids=processed_ids,
                                start_time=start_time,
                                timeout=timeout,
                            )

                        # 4. Stale detection for working states
                        working_states = {
                            "IN_PROGRESS", "PLANNING", "QUEUED", "PAUSED",
                        }
                        if jules_state in working_states:
                            stale_seconds = loop.time() - stale_state_time
                            if stale_seconds >= nudge_interval:
                                if stale_nudge_count < max_nudges:
                                    stale_nudge_count += 1
                                    logger.warning(
                                        f"Silent agent detected ({stale_seconds:.0f}s). "
                                        f"Sending nudge #{stale_nudge_count}/{max_nudges}..."
                                    )
                                    nudge_msg = (
                                        "[System Status Check] You have been in progress for 30 minutes. "
                                        "Please provide a very brief status update if you are still working, "
                                        "or continue to completion if you are almost finished."
                                    )
                                    try:
                                        await sdk.sessions.send_message(session_id, nudge_msg)
                                        stale_state_time = loop.time()
                                    except Exception as e:
                                        logger.error(f"Failed to send nudge: {e}")
                                        # Re-check state after failed nudge
                                        try:
                                            refreshed = await sdk.sessions.get(session_id)
                                            refreshed_state = self._get_state_str(refreshed)
                                            if refreshed_state in ("COMPLETED", "FAILED"):
                                                if refreshed_state == "COMPLETED":
                                                    return self._handle_completed(refreshed, session_name)
                                                return await self._handle_failed(
                                                    session_id=session_id,
                                                    session=refreshed,
                                                    session_name=session_name,
                                                    processed_ids=processed_ids,
                                                    start_time=start_time,
                                                    timeout=timeout,
                                                )
                                        except Exception:
                                            pass
                                else:
                                    msg = (
                                        f"Silent agent persistent: {session_name} in {jules_state} "
                                        f"for {max_nudges * nudge_interval / 60:.0f} minutes. "
                                        "Exhausted nudges."
                                    )
                                    logger.error(msg)
                                    raise JulesTimeoutError(msg)

                    except JulesTimeoutError:
                        raise
                    except JulesSessionError:
                        raise
                    except Exception as e:
                        logger.warning(f"Monitor loop error (transient): {e}")

                    await asyncio.sleep(poll_interval)

        except TimeoutError as e:
            msg = f"Wait for completion exceeded global timeout of {self.timeout}s."
            logger.error(msg)
            raise JulesTimeoutError(msg) from e

    # ── Monitor helper methods ──────────────────────────────────────────

    def _normalize_session_id(self, session_name: str) -> str:
        """Ensure session name has 'sessions/' prefix."""
        if not session_name.startswith("sessions/"):
            return f"sessions/{session_name}"
        return session_name

    def _get_state_str(self, session: Any) -> str:
        """Extract state string from session object."""
        state = session.state
        if isinstance(state, SessionState):
            return state.value
        return str(state)

    def _handle_completed(
        self, session: Any, session_name: str
    ) -> dict[str, Any]:
        """Extract PR URL from completed session and return success."""
        pr_url = None
        branch_name = None
        if session.outputs:
            for output in session.outputs:
                if output.pull_request and output.pull_request.url:
                    pr_url = output.pull_request.url
                    branch_name = getattr(output.pull_request, "headRef", None)
                    break

        if pr_url:
            console.print(f"\n[bold green]PR Created: {pr_url}[/bold green]")
        else:
            console.print(
                "\n[yellow]Session completed but no PR found in outputs.[/yellow]"
            )

        return {
            "status": "success",
            "pr_url": pr_url,
            "branch_name": branch_name,
            "session_name": session_name,
        }

    async def _handle_failed(
        self,
        session_id: str,
        session: Any,
        session_name: str,
        processed_ids: set[str],
        start_time: float,
        timeout: float,
    ) -> dict[str, Any]:
        """Handle FAILED state - extract error reason, attempt recovery."""
        error_msg = "Unknown error"

        # Fall back to checking activities via SDK
        try:
            activities = await self.sdk_client.activities.list_all(session_id)
            for act in activities:
                if act.session_failed and act.session_failed.get("reason"):
                    error_msg = act.session_failed["reason"]
                    break
        except Exception as e:
            logger.warning(f"Failed to fetch activities for failure reason: {e}")

        logger.error(f"Jules Session FAILED. Reason: {error_msg}")
        msg = f"Jules Session Failed: {error_msg}"
        raise JulesSessionError(msg)

    async def _handle_user_feedback(
        self,
        session_id: str,
        processed_ids: set[str],
        rejection_count: int,
        max_rejections: int,
        require_plan_approval: bool,
        plan_auditor: Any,
    ) -> bool:
        """Handle AWAITING_USER_FEEDBACK state.

        Checks for plan approval first (if required), then checks for
        regular inquiries (questions from Jules).

        Returns True if a message was sent (inquiry handled).
        """
        sdk = self.sdk_client

        # 1. Check for plan approval first
        if require_plan_approval:
            try:
                activities = await sdk.activities.list_all(session_id)
                for act in activities:
                    if act.plan_generated and act.name not in processed_ids:
                        plan_data = act.plan_generated
                        plan = plan_data.get("plan", {})
                        plan_id = plan.get("id")
                        if plan_id and plan_id not in processed_ids:
                            await self._review_and_approve_plan(
                                session_id, plan, plan_id,
                                processed_ids, rejection_count, max_rejections,
                                plan_auditor,
                            )
                            processed_ids.add(act.name)
                            processed_ids.add(plan_id)
                            return True
            except Exception as e:
                logger.warning(f"Failed to check for plan: {e}")

        # 2. Check for regular inquiry via activities
        try:
            activities = await sdk.activities.list_all(session_id)
            for act in activities:
                act_id = act.name or act.id
                if act_id in processed_ids:
                    continue
                msg = self._extract_inquiry_message(act)
                if msg:
                    processed_ids.add(act_id)
                    await self._answer_inquiry(session_id, msg)
                    return True
        except Exception as e:
            logger.warning(f"Failed to check for inquiry: {e}")

        return False

    async def _handle_plan_approval(
        self,
        session_id: str,
        processed_ids: set[str],
        rejection_count: int,
        max_rejections: int,
        plan_auditor: Any,
    ) -> None:
        """Handle AWAITING_PLAN_APPROVAL state."""
        sdk = self.sdk_client
        try:
            activities = await sdk.activities.list_all(session_id)
            for act in activities:
                if act.plan_generated and act.name not in processed_ids:
                    plan_data = act.plan_generated
                    plan = plan_data.get("plan", {})
                    plan_id = plan.get("id")
                    if plan_id and plan_id not in processed_ids:
                        await self._review_and_approve_plan(
                            session_id, plan, plan_id,
                            processed_ids, rejection_count, max_rejections,
                            plan_auditor,
                        )
                        processed_ids.add(act.name)
                        processed_ids.add(plan_id)
                        return
        except Exception as e:
            logger.warning(f"Failed to handle plan approval: {e}")

    async def _review_and_approve_plan(
        self,
        session_id: str,
        plan: dict[str, Any],
        plan_id: str,
        processed_ids: set[str],
        rejection_count: int,
        max_rejections: int,
        plan_auditor: Any,
    ) -> None:
        """Review a plan using PlanAuditor, approve or reject."""
        sdk = self.sdk_client

        if plan_auditor and rejection_count < max_rejections:
            console.print(
                f"\n[bold magenta]Plan Approval Requested:[/bold magenta] {plan_id}"
            )
            try:
                plan_text = plan.get("steps", [])
                plan_str = json.dumps(plan_text, indent=2)

                review_prompt = (
                    "Jules has generated an implementation plan. Please review it.\n\n"
                    f"{plan_str}\n\n"
                    "If the plan is acceptable, reply with just 'APPROVE'.\n"
                    "If there are issues, provide specific feedback."
                )

                audit_result = await plan_auditor.run(review_prompt)
                reply = audit_result.strip() if isinstance(audit_result, str) else str(audit_result.output).strip()

                if "APPROVE" in reply.upper() and len(reply) < 50:
                    console.print("[bold green]Plan Approved by Auditor.[/bold green]")
                    await sdk.sessions.approve_plan(session_id)
                else:
                    console.print("[bold yellow]Plan Rejected. Sending Feedback...[/bold yellow]")
                    rejection_count += 1
                    await sdk.sessions.send_message(session_id, reply)
            except Exception as e:
                logger.error(f"Plan audit failed: {e}")
                # Fallback: auto-approve
                await sdk.sessions.approve_plan(session_id)
        else:
            # Auto-approve if no auditor or max rejections reached
            if rejection_count >= max_rejections:
                console.print(
                    f"[bold yellow]Max plan rejections ({max_rejections}) reached. Auto-approving.[/bold yellow]"
                )
            await sdk.sessions.approve_plan(session_id)

    def _extract_inquiry_message(self, activity: Any) -> str | None:
        """Extract a question message from an activity.

        Only extracts from agentMessaged activities for AWAITING_USER_FEEDBACK.
        """
        if activity.agent_messaged:
            msg = activity.agent_messaged.get("agentMessage")
            if msg:
                return str(msg)
        return None

    async def _answer_inquiry(self, session_id: str, question: str) -> None:
        """Answer a Jules inquiry using the Manager Agent."""
        console.print(
            f"\n[bold magenta]Jules Question Detected:[/bold magenta] {question}"
        )
        console.print("[dim]Consulting Manager Agent with full context...[/dim]")

        try:
            enhanced_context = await self.context_builder.build_question_context(question)
            console.print(f"[dim]Context size: {len(enhanced_context)} chars[/dim]")

            from src.agents import get_manager_response

            reply_text = await get_manager_response(question, enhanced_context)

            followup = settings.read_template(
                "MANAGER_INQUIRY_FOLLOWUP.md",
                default="(System Note: If task complete/blocker resolved, proceed to create PR. Do not wait.)",
            )
            reply_text += f"\n\n{followup}"

            console.print(f"[bold cyan]Manager Agent Reply:[/bold cyan] {reply_text}")
            await self.sdk_client.sessions.send_message(session_id, reply_text)
            await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Manager Agent failed: {e}")
            fallback_template = settings.read_template(
                "MANAGER_INQUIRY_FALLBACK.md",
                default="I encountered an error processing your question. Original question: {{question}}",
            )
            fallback_msg = fallback_template.replace("{{question}}", question)
            await self.sdk_client.sessions.send_message(session_id, fallback_msg)

    async def get_latest_branch_commit(self, branch_name: str) -> str:
        """Get the latest commit hash for a branch via Git."""
        try:
            git = GitManager()
            try:
                output = await git._run_git(
                    ["ls-remote", "origin", f"refs/heads/{branch_name}"], check=True
                )
                if output and output.strip():
                    return output.strip().split()[0]
            except Exception as e:
                logger.debug(f"ls-remote failed for {branch_name}: {e}")

            try:
                return await git._run_git(["rev-parse", branch_name], check=True)
            except Exception:
                try:
                    return await git._run_git(["rev-parse", f"origin/{branch_name}"], check=True)
                except Exception:
                    await git.fetch_changes()
                    return await git._run_git(["rev-parse", f"origin/{branch_name}"], check=True)
        except Exception as e:
            logger.warning(f"Failed to get commit hash for {branch_name}: {e}")
            return "unknown"

    @retry_on_429(DispatcherConfig())
    async def get_session_state(self, session_id: str) -> str:
        """Get current state of a Jules session via SDK.

        Args:
            session_id: Session ID (with or without "sessions/" prefix).

        Returns:
            Official Jules API Session state string.
        """
        try:
            session = await self.sdk_client.sessions.get(session_id)
            return self._get_state_str(session)
        except Exception as e:
            logger.warning(f"Failed to get session state for {session_id}: {e}")
            return "UNKNOWN"

    async def send_message(self, session_id: str, content: str) -> None:
        """Sends a message to the active session via SDK."""
        await self._send_message(session_id, content)

    @retry_on_429(DispatcherConfig())
    async def _send_message(self, session_id: str, content: str) -> None:
        """Internal implementation for sending messages via SDK."""
        try:
            await self.sdk_client.sessions.send_message(session_id, content)
            console.print("[dim]Message sent.[/dim]")
            logger.info(f"Message sent to {session_id}")
        except Exception as e:
            logger.error(f"Failed to send message to {session_id}: {e!r}")
            raise

    async def approve_plan(self, session_id: str, plan_id: str | None = None) -> None:
        """Approves a plan in a session via SDK.

        Args:
            session_id: The session ID (with or without "sessions/" prefix).
            plan_id: Deprecated, SDK handles plan selection.
        """
        await self.sdk_client.sessions.approve_plan(session_id)
