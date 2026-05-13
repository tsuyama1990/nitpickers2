import re
from typing import Any

from rich.console import Console

from src.config import settings
from src.domain_models import CycleManifest
from src.enums import FlowStatus, WorkPhase
from src.services.jules_client import JulesClient
from src.state import CycleState
from src.state_manager import StateManager
from src.utils import logger

console = Console()


class BaseJulesUseCase:
    """Base class for use cases interacting with Jules sessions."""

    def __init__(self, jules_client: JulesClient) -> None:
        self.jules = jules_client

    def _build_instruction(
        self,
        cycle_id: str,
        current_phase: WorkPhase,
        state: CycleState,
        cycle_manifest: CycleManifest | None,
    ) -> str:
        """Helper to build prompt from templates based on phase and render variables."""
        if current_phase == WorkPhase.REFACTORING:
            template = settings.template_files.post_audit_refactor_instruction
        elif state.status in {FlowStatus.RETRY_FIX, FlowStatus.TDD_FAILED, FlowStatus.AUDIT_FAILED}:
            template = settings.template_files.audit_feedback_injection
        else:
            template = settings.template_files.coder_instruction

        instruction = settings.get_prompt_content(template)
        if not instruction:
            instruction = "Implement the requested features."

        # 1. Prepare feedback payload (with noise reduction for mechanical logs)
        feedback_val = ""
        if state.audit_result and state.audit_result.feedback:
            feedback_val = state.audit_result.feedback
        elif state.status == FlowStatus.TDD_FAILED and state.error:
            # SANITIZE: Remove environment/download noise from mechanical logs
            sanitized_error = self._sanitize_mechanical_logs(state.error)
            feedback_val = (
                f"# VERIFICATION FAILED: TECHNICAL ERROR\n\n"
                f"> [!CAUTION]\n"
                f"> The code passed the Auditor but failed technical verification (Lint/Tests). **Fix the following errors.**\n\n"
                f"## ERROR OUTPUT\n```text\n{sanitized_error}\n```\n\n"
                f"---\n"
                f"## INSTRUCTION\n"
                f"Resolve the technical failures shown above. Focus on the file paths and error codes provided."
            )

        # 2. Prepare variables for replacement
        pr_url = state.pr_url or (cycle_manifest.pr_url if cycle_manifest else "")

        # 3. Perform replacements
        instruction = instruction.replace("{{cycle_id}}", str(cycle_id))
        instruction = instruction.replace("{{feedback}}", feedback_val)
        instruction = instruction.replace("{{pr_url}}", pr_url)

        # 4. Handle simple conditional blocks like {{#pr_url}}...{{/pr_url}}
        if pr_url:
            # Remove the tags but keep the content
            instruction = re.sub(
                r"\{\{#pr_url\}\}(.*?)\{\{/pr_url\}\}", r"\1", instruction, flags=re.DOTALL
            )
        else:
            # Remove the entire block including content
            instruction = re.sub(
                r"\{\{#pr_url\}\}.*?\{\{/pr_url\}\}", "", instruction, flags=re.DOTALL
            )

        return instruction

    def _sanitize_mechanical_logs(self, raw_logs: str) -> str:
        """Strip environment-related noise from logs (Downloading, Installing, etc.)."""
        noise_patterns = [
            r"Downloading\s+.*",
            r"Downloaded\s+.*",
            r"Installed\s+\d+\s+packages.*",
            r"Creating virtual environment.*",
            r"Using CPython\s+.*",
            r"interpreter at:.*",
            r"Successfully installed.*",
            r"\[\+\]\s+\d+/\d+.*",
            r"Container\s+.*Creating",
            r"Container\s+.*Created",
            r"WARN\[\d+\]\s+.*",
            r"Download\s+.*",
        ]

        lines = raw_logs.splitlines()
        clean_lines = []
        for line in lines:
            if any(re.search(p, line) for p in noise_patterns):
                continue
            if not line.strip():
                continue
            clean_lines.append(line)

        return "\n".join(clean_lines)

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

    async def _run_jules_session(
        self,
        session_req_id: str,
        instruction: str,
        target_files: list[str],
        context_files: list[str],
        cycle_id: str,
        state_manager: StateManager,
        branch: str | None = None,
    ) -> tuple[str | None, dict[str, Any]]:
        """Launch a new Jules session and update cycle state."""
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
            state_manager.update_cycle_state(
                cycle_id, jules_session_id=jules_session_name, status="in_progress"
            )

        return jules_session_name, result
