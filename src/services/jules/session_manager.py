import asyncio
import logging
from typing import Any

from rich.console import Console

from src.services.jules_client import JulesClient

logger = logging.getLogger("AC-CDD")
console = Console()


class JulesSessionManager:
    """
    Manages high-level lifecycle of Jules sessions, including
    waiting for new work to start and polling for completion.
    """

    def __init__(self, jules_client: JulesClient) -> None:
        self.jules = jules_client

    async def wait_for_start(self, session_id: str, timeout_sec: int = 60) -> bool:
        """
        Waits for a Jules session to transition out of a terminal state
        into an active state (IN_PROGRESS, etc.) after sending new instructions.
        """
        logger.info(f"Waiting for session {session_id} to start new work...")

        # States that indicate Jules is actively working or preparing
        active_states = {
            "IN_PROGRESS",
            "QUEUED",
            "PLANNING",
            "AWAITING_PLAN_APPROVAL",
            "AWAITING_USER_FEEDBACK",
            "PAUSED",
        }

        interval = 5
        attempts = timeout_sec // interval

        for attempt in range(attempts):
            await asyncio.sleep(interval)
            state = await self.jules.get_session_state(session_id)
            logger.debug(f"Session {session_id} state check ({attempt + 1}/{attempts}): {state}")

            if state in active_states:
                logger.info(f"Jules session {session_id} is now active: {state}")
                return True

            if state == "FAILED":
                logger.error(f"Jules session {session_id} failed while waiting for start.")
                return False

        logger.warning(
            f"Jules session {session_id} did not transition to an active state after {timeout_sec}s. "
            "Proceeding assuming message received but state lagging."
        )
        return True

    async def poll_until_complete(
        self, session_id: str, expect_new_work: bool = False
    ) -> dict[str, Any]:
        """
        Polls a Jules session until it reaches a terminal state (COMPLETED/FAILED).
        """
        if expect_new_work:
            # Ensure we don't pick up a stale COMPLETED status from a previous turn
            await self.wait_for_start(session_id)

        logger.info(f"Monitoring Jules session {session_id} until completion...")
        return await self.jules.wait_for_completion(session_id)

    async def is_state_stale(
        self, session_id: str, current_state: str, last_change_time: float, timeout_sec: int = 300
    ) -> bool:
        """
        Checks if the current session state is likely 'stale' (leftover from previous turn).
        Returns True if we should keep waiting for a transition.
        Returns False if the state is fresh or we've waited too long.
        """
        import asyncio

        now = asyncio.get_running_loop().time()
        stale_seconds = now - last_change_time

        if stale_seconds >= timeout_sec:
            logger.warning(
                f"Session {session_id} stuck in {current_state} for {stale_seconds:.0f}s. "
                "Forcing transition."
            )
            return False

        # If it's in a terminal state but we just sent work, it's stale
        return current_state in {"COMPLETED", "FAILED"}
