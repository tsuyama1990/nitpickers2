import asyncio
import os
import sys
from typing import Any

from src.domain_models.tracing import TracingMetadata

try:
    import select
except ImportError:
    select = None  # type: ignore[assignment]

import uuid

import google.auth
import httpx
import litellm
from google.auth.transport.requests import Request as GoogleAuthRequest
from rich.console import Console

from src.agents import get_manager_agent
from src.config import settings
from src.domain_models.config import DispatcherConfig
from src.services.async_dispatcher import retry_on_429
from src.services.git_ops import GitManager
from src.utils import logger

from .jules.api import JulesApiClient
from .jules.context_builder import JulesContextBuilder
from .jules.git_context import JulesGitContext
from .jules.inquiry_handler import JulesInquiryHandler

# Tracing initialization is now handled by TracingService

console = Console()


# --- Exception Classes ---
class JulesSessionError(Exception):
    pass


class JulesTimeoutError(JulesSessionError):
    pass


class JulesApiError(Exception):
    pass


# --- API Client Implementation ---
# Moved to services/jules/api.py


# Global semaphore to serialize Jules session creation across all parallel cycles.
# The Jules API returns FAILED_PRECONDITION (400) when concurrent session creation
# requests are made from the same source. We allow only 1 in-flight creation at a
# time; once created, sessions are monitored concurrently as normal.
_session_creation_lock: asyncio.Semaphore = asyncio.Semaphore(1)


# --- Service Client Implementation ---
class JulesClient:
    """
    Client for interacting with the Google Cloud Code Agents API (Jules API).
    """

    def __init__(self, manager_agent: Any | None = None, plan_auditor: Any | None = None) -> None:
        self.project_id = settings.GCP_PROJECT_ID
        self.base_url = settings.jules.base_url
        self.timeout = settings.jules.timeout_seconds
        self.poll_interval = settings.jules.polling_interval_seconds
        self.console = Console()
        self.git = GitManager()
        self.test_mode = settings.test_mode

        api_key_to_use = settings.JULES_API_KEY.get_secret_value() or os.getenv("JULES_API_KEY")

        try:
            self.credentials, self.project_id_from_auth = google.auth.default()
            if not self.project_id:
                self.project_id = self.project_id_from_auth
        except Exception as e:
            if not api_key_to_use:
                logger.warning(
                    f"Could not load Google Credentials: {e}. Falling back to API Key if available."
                )
            else:
                logger.debug(f"Google ADC not found: {e}. Falling back to API Key.")
            self.credentials = None  # type: ignore[assignment]

        self.manager_agent = manager_agent if manager_agent else get_manager_agent()

        if plan_auditor:
            self.plan_auditor = plan_auditor
        else:
            from src.services.plan_auditor import PlanAuditor

            self.plan_auditor = PlanAuditor()

        if not api_key_to_use and self.credentials:
            api_key_to_use = self.credentials.token

        self.api_client = JulesApiClient(api_key=api_key_to_use)
        self.context_builder = JulesContextBuilder(self.git)
        self.git_context = JulesGitContext(self.git)
        self.inquiry_handler = JulesInquiryHandler(
            manager_agent=self.manager_agent, context_builder=self.context_builder, client_ref=self
        )

    async def _sleep(self, seconds: float) -> None:
        """Async sleep wrapper for easier mocking in tests."""
        await asyncio.sleep(seconds)

    async def list_activities(self, session_id_path: str) -> list[dict[str, Any]]:
        """Delegates activity listing to the API Client (async, non-blocking)."""
        return await self.api_client.list_activities_async(session_id_path)

    def _get_headers(self) -> dict[str, str]:
        # Reuse headers from api_client + auth if needed
        headers = self.api_client._get_headers()

        if self.credentials:
            if not self.credentials.valid:
                self.credentials.refresh(GoogleAuthRequest())  # type: ignore[no-untyped-call]
            headers["Authorization"] = f"Bearer {self.credentials.token or ''}"
        return headers

    async def run_session(
        self,
        session_id: str,
        prompt: str,
        files: list[str] | None = None,
        require_plan_approval: bool = False,
        branch: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Orchestrates the Jules session."""
        if not self.api_client.api_key and "PYTEST_CURRENT_TEST" not in os.environ:
            errmsg = "Missing JULES_API_KEY or ADC credentials."
            raise JulesSessionError(errmsg)

        owner, repo_name, context_branch = await self.git_context.prepare_git_context(branch=branch)
        branch_to_use = branch or context_branch
        full_prompt = self.context_builder.construct_run_prompt(
            prompt, files, extra.get("target_files"), extra.get("context_files")
        )

        payload = {
            "prompt": full_prompt,
            "sourceContext": {
                "source": f"sources/github/{owner}/{repo_name}",
                "githubRepoContext": {"startingBranch": branch_to_use},
            },
            "automationMode": "AUTO_CREATE_PR",
            "requirePlanApproval": require_plan_approval,
        }
        if "title" in extra:
            payload["title"] = str(extra["title"])

        session_name = await self._create_jules_session(payload)

        # Return immediately; caller should use wait_for_completion
        return {"session_name": session_name, "status": "running"}

    async def _create_jules_session(self, payload: dict[str, Any]) -> str:
        """Wrapper to call create_session via api_client.

        Acquires _session_creation_lock before calling the Jules API to prevent
        concurrent session creation across parallel cycles, which causes a
        FAILED_PRECONDITION (400) error from the API.
        """
        prompt = str(payload.get("prompt", ""))
        source_context = payload.get("sourceContext", {})
        source = str(source_context.get("source", ""))

        repo_context = source_context.get("githubRepoContext", {})
        branch = str(repo_context.get("startingBranch", "main"))

        require_approval = bool(payload.get("requirePlanApproval", False))
        title = payload.get("title")
        automation_mode = str(payload.get("automationMode", "AUTO_CREATE_PR"))

        async with _session_creation_lock:
            logger.info(
                f"Creating Jules session for branch '{branch}' (serialized via _session_creation_lock)"
            )
            resp = await self.api_client.create_session(
                source,
                prompt,
                require_approval,
                branch=branch,
                title=str(title) if title else None,
                automation_mode=automation_mode,
            )
            # Rate-limit buffer: Jules API rejects with FAILED_PRECONDITION when too many
            # sessions are created in a short window. A 10-second pause ensures the API
            # has plenty of time to stabilize each creation before the next one starts.
            await asyncio.sleep(10)
        return str(resp.get("name", ""))

    async def continue_session(self, session_name: str, prompt: str) -> dict[str, Any]:
        """Continues an existing session using LangGraph-based monitoring."""
        logger.info(f"Continuing Session {session_name} with info...")
        try:
            await self._send_message(session_name, prompt)
        except Exception as e:
            logger.error(f"Failed to send message to session {session_name}: {e!r}")
            raise

        logger.info(f"Waiting for Jules to process feedback for {session_name}...")

        # We must wait for completion via LangGraph which handles inquiries and stale states.
        # Since we just sent a message, we explicitly expect new work (transition from current state).
        result = await self.wait_for_completion(session_name, expect_new_work=True)
        result["session_name"] = session_name
        return result

    async def wait_for_completion(
        self,
        session_name: str,
        require_plan_approval: bool = False,
        expect_new_work: bool = False,
    ) -> dict[str, Any]:
        """Wait for Jules session completion using LangGraph state management.

        Args:
            session_name: The session to wait for.
            require_plan_approval: Whether to wait for and approve plans.
            expect_new_work: If True, the system will wait for Jules to move AWAY from
                             its current state (e.g. COMPLETED) before starting to
                             look for completion again. Useful after sending a message.
        """
        from langchain_core.runnables import RunnableConfig

        from src.jules_session_graph import build_jules_session_graph
        from src.jules_session_state import JulesSessionState

        self.console.print(
            f"[bold green]Jules is working... (Session: {session_name})[/bold green]"
        )
        self.console.print(
            "[dim]Type your message and press Enter at any time to chat with Jules.[/dim]"
        )

        session_url = self._get_session_url(session_name)

        # 1. Check current state.
        current_state = await self.get_session_state(session_name)
        if expect_new_work and current_state == "COMPLETED":
            logger.info(
                f"Session {session_name} is currently COMPLETED. Waiting for Jules to resume work..."
            )

        # 2. Initialize processed IDs (important for ignoring previous Turn's activities)
        processed_ids: set[str] = set()
        processed_completion_ids: set[str] = set()
        await self._initialize_processed_ids(
            session_url, processed_ids, processed_completion_ids=processed_completion_ids
        )

        # Build graph
        graph = build_jules_session_graph(self)

        # Create initial state
        initial_state = JulesSessionState(
            session_url=session_url,
            session_name=session_name,
            start_time=asyncio.get_running_loop().time(),
            timeout_seconds=self.timeout,
            poll_interval=self.poll_interval,
            require_plan_approval=require_plan_approval,
            fallback_max_wait=settings.jules.wait_for_pr_timeout_seconds,
            processed_activity_ids=processed_ids,
            processed_completion_ids=processed_completion_ids,
            jules_state=current_state,
            expect_new_work=expect_new_work,
        )

        # Run graph
        metadata = TracingMetadata(
            session_id=f"jules-{session_name}", execution_type="jules_session"
        )
        tracing_config = settings.tracing_service.get_run_config(metadata)

        config = RunnableConfig(
            configurable={"thread_id": f"jules-{session_name}"},
            recursion_limit=settings.GRAPH_RECURSION_LIMIT,
            **tracing_config,  # type: ignore[typeddict-item]
        )

        try:
            async with asyncio.timeout(self.timeout):
                final_state = await graph.ainvoke(initial_state, config)  # type: ignore[attr-defined]
        except TimeoutError as e:
            msg = f"Wait for completion exceeded global timeout of {self.timeout}s."
            logger.error(msg)
            raise JulesTimeoutError(msg) from e

        # Handle final state
        # LangGraph may return dict or object
        def _get(obj: Any, key: str) -> Any:
            return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)

        status = _get(final_state, "status")

        if status == "success":
            return {
                "status": "success",
                "pr_url": _get(final_state, "pr_url"),
                "branch_name": _get(final_state, "branch_name"),
                "raw": _get(final_state, "raw_data"),
            }

        error_msg = _get(final_state, "error") or "Session failed"

        if status == "failed":
            raise JulesSessionError(error_msg)
        if status == "timeout":
            msg = f"Session timed out. Last error: {error_msg}"
            raise JulesTimeoutError(msg)

        msg = f"Session ended in unexpected state: {status}"
        raise JulesSessionError(msg)

    async def get_latest_branch_commit(self, branch_name: str) -> str:
        """Get the latest commit hash for a branch via Git."""
        try:
            git = GitManager()
            # 1. First, always try to get the true remote commit since Jules pushes to remote
            try:
                # Target refs/heads explicitly to avoid ambiguous ref matches (e.g. pull requests)
                output = await git._run_git(
                    ["ls-remote", "origin", f"refs/heads/{branch_name}"], check=True
                )
                if output and output.strip():
                    # The first column is the commit hash
                    return output.strip().split()[0]
            except Exception as e:
                logger.debug(f"ls-remote failed for {branch_name}: {e}")

            # 2. Try local rev-parse as fallback
            try:
                return await git._run_git(["rev-parse", branch_name], check=True)
            except Exception:
                # 3. Try origin/branch_name
                try:
                    return await git._run_git(["rev-parse", f"origin/{branch_name}"], check=True)
                except Exception:
                    # 4. Last ditch: fetch and try again
                    await git.fetch_changes()
                    return await git._run_git(["rev-parse", f"origin/{branch_name}"], check=True)
        except Exception as e:
            logger.warning(f"Failed to get commit hash for {branch_name}: {e}")
            return "unknown"

    def _get_session_url(self, session_name: str) -> str:
        base_url = self.base_url.rstrip("/")
        if session_name.startswith("sessions/"):
            return f"{base_url}/{session_name}"
        return f"{base_url}/sessions/{session_name}"

    @retry_on_429(DispatcherConfig())
    async def get_session_state(self, session_id: str) -> str:
        """Get current state of Jules session.

        Args:
            session_id: Session ID (with or without "sessions/" prefix)

        Returns:
            Official Jules API Session state:
              - QUEUED: Session is queued
              - PLANNING: Jules is planning
              - AWAITING_PLAN_APPROVAL: Waiting for plan approval
              - AWAITING_USER_FEEDBACK: Jules has a question
              - IN_PROGRESS: Jules is actively working
              - PAUSED: Session is paused
              - FAILED: Session failed
              - COMPLETED: Session completed (may or may not have PR)
              - STATE_UNSPECIFIED: Unknown state
              - UNKNOWN: Could not retrieve state (network error etc.)
        """
        session_url = self._get_session_url(session_id)

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    session_url, headers=self._get_headers(), timeout=settings.jules.request_timeout
                )
                resp.raise_for_status()
                data = resp.json()
                return str(data.get("state", "UNKNOWN"))
            except Exception as e:
                logger.warning(f"Failed to get session state for {session_id}: {e}")
                return "UNKNOWN"

    async def _initialize_processed_ids(  # noqa: C901
        self,
        session_url: str,
        processed_ids: set[str],
        processed_completion_ids: set[str] | None = None,
    ) -> None:
        try:
            state = "UNKNOWN"
            initial_acts = []

            # Fetch session state and early activities via httpx to respect test mocks
            try:
                async with httpx.AsyncClient() as client:
                    session_resp = await client.get(
                        session_url,
                        headers=self._get_headers(),
                        timeout=settings.jules.request_timeout,
                    )
                    if session_resp.status_code == httpx.codes.OK:
                        state = session_resp.json().get("state", "UNKNOWN")

                # Fetch all activities using pagination
                initial_acts = await self.list_activities(session_url)
            except Exception as e:
                logger.warning(f"Failed to fetch initial session data: {e}")

            latest_inquiry_id = None
            latest_ts = ""

            for act in initial_acts:
                act_id = act.get("name")
                if not act_id:
                    continue

                processed_ids.add(act_id)

                if processed_completion_ids is not None and "sessionCompleted" in act:
                    processed_completion_ids.add(act_id)

                # If awaiting feedback, track the latest inquiry
                if state == "AWAITING_USER_FEEDBACK":
                    msg = self.inquiry_handler.extract_activity_message(act, jules_state=state)
                    if msg:
                        ts = act.get("createTime", "")
                        if ts >= latest_ts:
                            latest_ts = ts
                            latest_inquiry_id = act_id

            # If we are waiting for feedback, ensure the latest inquiry is NOT ignored
            if latest_inquiry_id:
                processed_ids.discard(latest_inquiry_id)
                logger.info(
                    f"Session is {state}: Re-enabling latest inquiry {latest_inquiry_id} for processing."
                )

            logger.info(f"Initialized with {len(processed_ids)} existing activities to ignore.")
        except Exception as e:
            logger.warning(f"Failed to fetch initial activities: {e}")

    async def _handle_manual_input(self, session_url: str) -> None:
        if not select:
            return
        try:
            if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                line = sys.stdin.readline()
                if line:
                    user_msg = line.strip()
                    if user_msg:
                        self.console.print(f"[dim]Sending: {user_msg}[/dim]")
                        await self._send_message(session_url, user_msg)
        except Exception:
            logger.debug("Non-blocking input check failed.")

    async def send_message(self, session_url: str, content: str) -> None:
        """Sends a message to the active session."""
        await self._send_message(session_url, content)

    @retry_on_429(DispatcherConfig())
    async def _send_message(self, session_url: str, content: str) -> None:
        """Internal implementation for sending messages."""
        if not session_url.startswith("http"):
            session_url = self._get_session_url(session_url)

        url = f"{session_url}{settings.jules.send_message_action}"
        payload = {"prompt": content}

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(url, json=payload, headers=self._get_headers())
                resp.raise_for_status()

                if resp.status_code == httpx.codes.OK:
                    self.console.print("[dim]Message sent.[/dim]")
                    logger.info(f"DEBUG: Message sent successfully to {url}")
                else:
                    self.console.print(
                        f"[bold red]Failed to send message: {resp.status_code}[/bold red]"
                    )
                    logger.error(f"SendMessage failed with status {resp.status_code}: {resp.text}")
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Jules API HTTP error {e.response.status_code} for {url}: {e.response.text}"
                )
                raise
            except Exception as e:
                logger.error(f"Unexpected error sending message to {url}: {e!r}")
                raise

    async def get_latest_plan(self, session_id: str) -> dict[str, Any] | None:
        """Fetches the latest 'planGenerated' activity."""
        session_id_path = (
            session_id if session_id.startswith("sessions/") else f"sessions/{session_id}"
        )
        activities = await self.list_activities(session_id_path)
        target_activity = settings.jules.plan_generated_activity
        for activity in activities:
            if target_activity in activity:
                return dict(activity.get(target_activity, {}))
        return None

    async def wait_for_activity_type(
        self,
        session_id: str,
        target_type: str,
        timeout_seconds: int | None = None,
        interval: int = 10,
    ) -> dict[str, Any] | None:
        """Polls for a specific activity type with timeout."""
        session_id_path = (
            session_id if session_id.startswith("sessions/") else f"sessions/{session_id}"
        )
        timeout_to_use = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.jules.activity_polling_timeout
        )
        try:
            async with asyncio.timeout(timeout_to_use):
                current_interval = interval
                while True:
                    activities = await self.list_activities(session_id_path)
                    for activity in activities:
                        if target_type in activity:
                            return activity
                    await self._sleep(current_interval)
                    # Exponential backoff with 60s cap
                    current_interval = min(current_interval * 2, 60)
        except TimeoutError:
            return None

    async def approve_plan(self, session_id: str, plan_id: str) -> dict[str, Any]:
        """Approves the specific plan."""
        session_id_path = (
            session_id if session_id.startswith("sessions/") else f"sessions/{session_id}"
        )
        return self.api_client.approve_plan(session_id_path, plan_id)

    def create_master_integrator_session(self) -> str:
        """
        Creates a new session ID for the Master Integrator.
        This session is entirely local (via litellm) and stateful,
        to resolve merge conflicts without spawning full Jules PR sessions.
        """
        return f"{settings.jules.master_integrator_prefix}{uuid.uuid4().hex[:8]}"

    async def send_message_to_session(
        self,
        session_id: str,
        message: str,
        message_history: list[dict[str, str]] | None = None,
        model: str | None = None,
        response_format: type[Any] | None = None,
    ) -> str:
        """
        Sends a message to the stateful Master Integrator session.
        Uses litellm for direct interaction.
        """
        messages = message_history if message_history is not None else []

        # Add the new message
        messages.append({"role": "user", "content": message})

        # Inject default model if none provided without hard-relying strictly on settings globally inside the method
        if not model:
            from src.config import settings

            model = settings.reviewer.smart_model

        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": settings.reviewer.master_integrator_temperature,
                "metadata": {"tags": ["master_integrator"], "session_id": session_id},
            }
            if response_format:
                kwargs["response_format"] = response_format

            response = await litellm.acompletion(**kwargs)
        except Exception as e:
            logger.error(f"Failed to communicate with LLM for Master Integrator: {e}")
            msg = f"LLM API error: {e}"
            raise JulesSessionError(msg) from e
        else:
            content_str = str(response.choices[0].message.content)

            # Append the assistant's response to history if provided
            if message_history is not None:
                message_history.append({"role": "assistant", "content": content_str})

            return content_str

    async def _create_manual_pr(self, session_url: str) -> str | None:
        """
        Ask Jules to commit changes and create PR when auto-PR creation fails.

        Returns PR URL if successful, None otherwise.
        """
        try:
            self.console.print("[cyan]Sending message to Jules to commit and create PR...[/cyan]")

            message = settings.read_template(settings.jules.pr_creation_template)

            await self._send_message(session_url, message)

            # Wait for Jules to process and create PR
            self.console.print("[dim]Waiting for Jules to create PR...[/dim]")

            # Poll for PR creation (max 5 minutes)
            import asyncio

            max_wait = settings.jules.wait_for_pr_timeout_seconds
            poll_interval = settings.jules.polling_interval_seconds
            elapsed = 0
            processed_fallback_ids: set[str] = set()

            while elapsed < max_wait:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

                # Check for PR and new activities
                try:
                    activities = await self.list_activities(session_url)
                    for activity in activities:
                        # Check for PR
                        if "pullRequest" in activity:
                            pr_url: str | None = activity["pullRequest"].get("url")
                            if pr_url:
                                self.console.print(f"[bold green]PR Created: {pr_url}[/bold green]")
                                return pr_url

                        # Log new activities to show progress
                        act_id = activity.get("name", activity.get("id"))
                        if act_id and act_id not in processed_fallback_ids:
                            msg = self.inquiry_handler.extract_activity_message(activity)
                            if msg:
                                self.console.print(f"[dim]Jules: {msg}[/dim]")
                            processed_fallback_ids.add(act_id)

                except Exception as e:
                    logger.debug(f"Error checking for PR/activities: {e}")

                if elapsed % settings.jules.progress_update_interval == 0:
                    self.console.print(
                        f"[dim]Still waiting for PR... ({elapsed}/{max_wait}s elapsed)[/dim]"
                    )

        except Exception as e:
            logger.error(f"Error requesting Jules to create PR: {e}")
            return None

        logger.warning(f"Timeout ({max_wait}s) waiting for Jules to create PR")
        return None
