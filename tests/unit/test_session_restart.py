from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.enums import FlowStatus
from src.services.coder_usecase import CoderUseCase
from src.services.jules_client import JulesSessionError
from src.state import CycleState


class TestSessionRestart:
    """Test session restart logic on failure."""

    @pytest.fixture
    def mock_jules(self) -> MagicMock:
        jules = MagicMock()
        jules.run_session = AsyncMock()
        jules.wait_for_completion = AsyncMock()
        jules.get_latest_branch_commit = AsyncMock()
        return jules

    @pytest.fixture
    def mock_manifest(self) -> MagicMock:
        manifest = MagicMock()
        manifest.jules_session_id = None
        manifest.session_restart_count = 0
        manifest.max_session_restarts = 2
        manifest.current_iteration = 1
        return manifest

    @pytest.mark.asyncio
    async def test_session_restart_on_failure(
        self, mock_jules: MagicMock, mock_manifest: MagicMock
    ) -> None:
        """Should restart session when Jules fails, up to max_session_restarts."""
        state = CycleState(cycle_id="01")
        state.iteration_count = 1
        call_count = 0

        def run_session_side_effect(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"session_name": "sessions/fail_123", "status": "running"}
            return {"session_name": "sessions/success_456", "status": "running"}

        def wait_for_completion_side_effect(session_id):  # type: ignore[no-untyped-def]
            if "fail" in session_id:
                error_msg = "Jules Session Failed: Unknown error"
                raise JulesSessionError(error_msg)
            return {"status": "success", "pr_url": "https://github.com/pr/1"}

        mock_jules.run_session.side_effect = run_session_side_effect
        mock_jules.wait_for_completion.side_effect = wait_for_completion_side_effect

        usecase = CoderUseCase(mock_jules)
        update_calls = []

        def track_updates(cycle_id, **kwargs):  # type: ignore[no-untyped-def]
            update_calls.append(kwargs)
            if "session_restart_count" in kwargs:
                mock_manifest.session_restart_count = kwargs["session_restart_count"]
            if "jules_session_id" in kwargs:
                mock_manifest.jules_session_id = kwargs["jules_session_id"]

        with patch("src.services.coder_usecase.StateManager") as MockManager:
            instance = MockManager.return_value
            instance.get_cycle.return_value = mock_manifest
            instance.update_cycle_state.side_effect = track_updates

            with patch("src.services.coder_usecase.settings") as mock_settings:
                mock_settings.get_template.return_value.read_text.return_value = "Instruction"
                mock_settings.get_target_files.return_value = []
                mock_settings.get_context_files.return_value = []
                mock_settings.SESSION_ID_PATTERN = r"^[A-Za-z0-9_\-]+$"
                result = await usecase.execute(state)

        assert result["status"] == FlowStatus.CODER_RETRY

        with patch("src.services.coder_usecase.StateManager") as MockManager2:
            instance2 = MockManager2.return_value
            instance2.get_cycle.return_value = mock_manifest

            with patch("src.services.coder_usecase.settings") as mock_settings2:
                mock_settings2.get_template.return_value.read_text.return_value = "Instruction"
                mock_settings2.get_target_files.return_value = []
                mock_settings2.get_context_files.return_value = []
                mock_settings2.SESSION_ID_PATTERN = r"^[A-Za-z0-9_\-]+$"
                result2 = await usecase.execute(state)

        assert result2["status"] == FlowStatus.READY_FOR_SELF_CRITIC
        assert result2["session"].pr_url == "https://github.com/pr/1"
        assert mock_jules.run_session.call_count == 2
        assert any(
            "session_restart_count" in call and call["session_restart_count"] == 1
            for call in update_calls
        )

    @pytest.mark.asyncio
    async def test_session_restart_max_limit(
        self, mock_jules: MagicMock, mock_manifest: MagicMock
    ) -> None:
        """Should fail after max_session_restarts attempts (forced minimum 4)."""
        state = CycleState(cycle_id="01")
        state.iteration_count = 1

        mock_jules.run_session.return_value = {
            "session_name": "sessions/fail_123",
            "status": "running",
        }
        mock_jules.wait_for_completion.side_effect = JulesSessionError(
            "Jules Session Failed: Unknown error"
        )

        usecase = CoderUseCase(mock_jules)

        # Set restart count to 4 (forced minimum is 4, so this is the threshold)
        mock_manifest.session_restart_count = 4
        mock_manifest.max_session_restarts = 2

        with patch("src.services.coder_usecase.StateManager") as MockManager:
            instance = MockManager.return_value
            instance.get_cycle.return_value = mock_manifest

            with patch("src.services.coder_usecase.settings") as mock_settings:
                mock_settings.get_template.return_value.read_text.return_value = "Instruction"
                mock_settings.get_target_files.return_value = []
                mock_settings.get_context_files.return_value = []
                mock_settings.SESSION_ID_PATTERN = r"^[A-Za-z0-9_\-]+$"
                result = await usecase.execute(state)

        assert result["status"] == FlowStatus.FAILED
        assert "Unknown error" in result["error"]
