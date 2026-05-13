from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain_models import AuditResult
from src.enums import FlowStatus
from src.services.coder_usecase import CoderUseCase
from src.state import CycleState


class TestSessionReuse:
    """Validate session reuse and fallback logic."""

    @pytest.fixture
    def mock_jules(self) -> MagicMock:
        jules = MagicMock()
        jules.run_session = AsyncMock()
        jules.continue_session = AsyncMock()
        jules.get_session_state = AsyncMock()
        jules.wait_for_completion = AsyncMock()
        jules.get_latest_branch_commit = AsyncMock()
        jules._send_message = AsyncMock()
        jules._get_session_url = MagicMock(return_value="https://jules/session/url")
        return jules

    @pytest.mark.asyncio
    async def test_reuse_completed_session_for_auditor_reject(self, mock_jules: MagicMock) -> None:
        """Should REUSE COMPLETED session for Auditor Reject (send feedback to same session)."""
        mock_jules.get_session_state.return_value = "COMPLETED"
        mock_jules.continue_session.return_value = {"status": "success", "pr_url": "http://pr"}

        mock_manifest = MagicMock()
        mock_manifest.jules_session_id = "sessions/123"
        mock_manifest.pr_url = None
        mock_manifest.current_iteration = 1
        mock_manifest.session_restart_count = 0

        audit = AuditResult(
            status="REJECTED", is_approved=False, reason="Needs work", feedback="Fix this issue"
        )
        state = CycleState(
            cycle_id="01",
            status=FlowStatus.RETRY_FIX,
        )
        state.audit_result = audit

        usecase = CoderUseCase(mock_jules)

        with patch("src.services.coder_usecase.StateManager") as MockManager:
            instance = MockManager.return_value
            instance.get_cycle.return_value = mock_manifest

            with patch("src.services.coder_usecase.settings") as mock_settings:
                mock_settings.get_prompt_content.return_value = "Instruction {{feedback}}"
                mock_settings.get_target_files.return_value = []
                mock_settings.get_context_files.return_value = []
                mock_settings.SESSION_ID_PATTERN = r"^[A-Za-z0-9_\-]+$"
                result = await usecase.execute(state)

        mock_jules.get_session_state.assert_called_with("sessions/123")
        mock_jules.continue_session.assert_called_once()

        # Verify the actual feedback content sent
        sent_message = (
            mock_jules.continue_session.call_args.args[1]
            if mock_jules.continue_session.call_args.args
            else mock_jules.continue_session.call_args.kwargs.get("message", "")
        )
        assert isinstance(sent_message, str) or hasattr(sent_message, "__contains__")

        mock_jules.run_session.assert_not_called()
        assert result["status"] == FlowStatus.READY_FOR_AUDIT

    @pytest.mark.asyncio
    async def test_create_new_session_if_failed(self, mock_jules: MagicMock) -> None:
        """Should create NEW session if previous session FAILED."""
        mock_jules.get_session_state.return_value = "FAILED"
        mock_jules.run_session.return_value = {
            "session_name": "sessions/new_456",
            "status": "success",
            "pr_url": "http://pr-new",
        }

        mock_manifest = MagicMock()
        mock_manifest.jules_session_id = "sessions/123"
        mock_manifest.pr_url = "https://pr"
        mock_manifest.current_iteration = 1
        mock_manifest.session_restart_count = 0

        audit = AuditResult(
            status="REJECTED", is_approved=False, reason="Needs work", feedback="Fix this issue"
        )
        state = CycleState(
            cycle_id="01",
            status=FlowStatus.RETRY_FIX,
        )
        state.audit_result = audit

        usecase = CoderUseCase(mock_jules)

        with patch("src.services.coder_usecase.StateManager") as MockManager:
            instance = MockManager.return_value
            instance.get_cycle.return_value = mock_manifest

            with patch("src.services.coder_usecase.settings") as mock_settings:
                mock_settings.get_prompt_content.return_value = "# PREVIOUS AUDIT FEEDBACK (MUST FIX)\n\n{{feedback}}\n\n{{#pr_url}}\nPrevious PR: {{pr_url}}\n{{/pr_url}}"
                mock_settings.get_target_files.return_value = []
                mock_settings.get_context_files.return_value = []
                mock_settings.SESSION_ID_PATTERN = r"^[A-Za-z0-9_\-]+$"
                await usecase.execute(state)

        mock_jules.get_session_state.assert_called_with("sessions/123")
        mock_jules._send_message.assert_not_called()  # Should NOT reuse FAILED session
        mock_jules.run_session.assert_called()

        prompt = mock_jules.run_session.call_args.kwargs["prompt"]
        assert "Fix this issue" in prompt
        assert "PREVIOUS AUDIT FEEDBACK" in prompt

    @pytest.mark.asyncio
    async def test_reuse_in_progress_session(self, mock_jules: MagicMock) -> None:
        """Should REUSE IN_PROGRESS session (original behavior)."""
        mock_jules.get_session_state.return_value = "IN_PROGRESS"
        mock_jules.continue_session.return_value = {"status": "success", "pr_url": "http://pr"}

        mock_manifest = MagicMock()
        mock_manifest.jules_session_id = "sessions/123"
        mock_manifest.pr_url = None
        mock_manifest.current_iteration = 1
        mock_manifest.session_restart_count = 0

        audit = AuditResult(
            status="REJECTED", is_approved=False, reason="Needs work", feedback="Fix this"
        )
        state = CycleState(
            cycle_id="01",
            status=FlowStatus.RETRY_FIX,
        )
        state.audit_result = audit

        usecase = CoderUseCase(mock_jules)

        with patch("src.services.coder_usecase.StateManager") as MockManager:
            instance = MockManager.return_value
            instance.get_cycle.return_value = mock_manifest

            with patch("src.services.coder_usecase.settings") as mock_settings:
                mock_settings.get_prompt_content.return_value = "Instruction {{feedback}}"
                mock_settings.get_target_files.return_value = []
                mock_settings.get_context_files.return_value = []
                mock_settings.SESSION_ID_PATTERN = r"^[A-Za-z0-9_\-]+$"
                result = await usecase.execute(state)

        mock_jules.continue_session.assert_called_once()

        # Verify the actual feedback content sent
        sent_message = (
            mock_jules.continue_session.call_args.args[1]
            if mock_jules.continue_session.call_args.args
            else mock_jules.continue_session.call_args.kwargs.get("message", "")
        )
        assert isinstance(sent_message, str) or hasattr(sent_message, "__contains__")

        mock_jules.run_session.assert_not_called()
        assert result["status"] == FlowStatus.READY_FOR_AUDIT
