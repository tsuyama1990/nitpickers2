import uuid
from pathlib import Path
from typing import Any

import litellm

from src.config import settings
from src.domain_models import ConflictRegistryItem, ConflictResolutionSchema
from src.services.conflict_manager import ConflictManager, ConflictMarkerRemainsError
from src.services.file_ops import FilePatcher
from src.state import IntegrationState
from src.utils import logger


class MaxRetriesExceededError(Exception):
    pass


class MasterIntegratorClient:
    """Local LLM-based session for resolving merge conflicts.

    Uses litellm directly (not related to Jules API). This is a lightweight,
    stateless conversation with an LLM to resolve git conflict markers.
    """

    def __init__(self) -> None:
        self.prefix = settings.jules.master_integrator_prefix

    def create_session(self) -> str:
        """Create a new session ID for the Master Integrator."""
        return f"{self.prefix}{uuid.uuid4().hex[:8]}"

    async def send_message(
        self,
        session_id: str,
        message: str,
        message_history: list[dict[str, str]] | None = None,
        model: str | None = None,
        response_format: type[Any] | None = None,
    ) -> str:
        """Send a message to the stateful Master Integrator session.

        Uses litellm for direct interaction (not related to Jules API).
        """
        messages = message_history if message_history is not None else []
        messages.append({"role": "user", "content": message})

        if not model:
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
            raise RuntimeError(msg) from e
        else:
            content_str = str(response.choices[0].message.content)
            if message_history is not None:
                message_history.append({"role": "assistant", "content": content_str})
            return content_str


class IntegrationUsecase:
    def __init__(
        self, master_integrator: MasterIntegratorClient | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.master_integrator = master_integrator or MasterIntegratorClient()
        self.conflict_manager = ConflictManager()
        self.file_ops = FilePatcher()

        if max_retries is not None:
            self.max_retries = max_retries
        else:
            try:
                from src.config import settings

                self.max_retries = settings.max_audit_retries + 1
            except ImportError:
                self.max_retries = 3

    async def run_integration_loop(
        self, state: IntegrationState, repo_path: Path
    ) -> IntegrationState:
        """
        Runs the Master Integrator loop.
        Sends unresolved conflicts sequentially to the stateful LLM session.
        Validates the output. If markers remain, retries up to max limits.
        """
        # Ensure session exists
        if not state.master_integrator_session_id:
            state.master_integrator_session_id = self.master_integrator.create_session()
            logger.info(f"Created Master Integrator Session: {state.master_integrator_session_id}")

        for i, item in enumerate(state.unresolved_conflicts):
            if item.resolved:
                continue

            try:
                await self._resolve_single_file(state.master_integrator_session_id, item, repo_path)
            except Exception as e:
                logger.error(f"Failed to resolve file {item.file_path}: {e}")
                msg = f"Failed to resolve {item.file_path}: {e}"
                raise MaxRetriesExceededError(msg) from e

            state.unresolved_conflicts[i] = item

        return state

    async def _resolve_single_file(
        self, session_id: str, item: ConflictRegistryItem, repo_path: Path
    ) -> None:
        max_retries = self.max_retries
        message_history: list[dict[str, str]] = []

        prompt = await self.conflict_manager.build_conflict_package(item, repo_path)

        while item.resolution_attempts < max_retries:
            item.resolution_attempts += 1
            logger.info(
                f"Resolving {item.file_path} (Attempt {item.resolution_attempts}/{max_retries})"
            )

            # Send to local LLM via Master Integrator
            response_json = await self.master_integrator.send_message(
                session_id, prompt, message_history, response_format=ConflictResolutionSchema
            )

            try:
                # Parse JSON output strictly via Pydantic model
                resolution = ConflictResolutionSchema.model_validate_json(response_json)
                clean_code = resolution.resolved_code
            except Exception as e:
                logger.warning(f"Failed to parse JSON response for {item.file_path}: {e}")
                prompt = "Your previous output was invalid JSON. You must conform exactly to the ConflictResolutionSchema."
                continue

            # Apply to file
            target_file = repo_path / item.file_path
            target_file.write_text(clean_code, encoding="utf-8")

            # Validate
            try:
                if self.conflict_manager.validate_resolution(target_file):
                    logger.info(f"Successfully resolved {item.file_path}.")
                    item.resolved = True
                    return
            except ConflictMarkerRemainsError as e:
                logger.warning(f"Resolution failed for {item.file_path}: {e}")
                prompt = (
                    "Your resolution failed. Conflict markers `<<<<<<<` still exist. "
                    "Fix it. Ensure the output does not contain standard Git conflict markers."
                )

        # If loop exits without returning, max retries reached.
        msg = f"Maximum conflict retries exceeded for {item.file_path}."
        raise MaxRetriesExceededError(msg)
