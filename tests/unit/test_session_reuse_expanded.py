from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain_models import CycleManifest
from src.enums import FlowStatus
from src.services.coder_usecase import CoderUseCase
from src.state import CycleState


@pytest.mark.asyncio
async def test_workflow_initial_pr_triggers_self_critic() -> None:
    """Verify that the first PR triggers READY_FOR_SELF_CRITIC."""
    mock_jules = MagicMock()
    mock_jules.wait_for_completion = AsyncMock(
        return_value={"status": "success", "pr_url": "http://pr/initial"}
    )
    mock_jules.get_latest_branch_commit = AsyncMock()

    usecase = CoderUseCase(mock_jules)
    state = CycleState(cycle_id="01")
    state.status = None  # Initial status

    with (
        patch.object(
            usecase,
            "_run_jules_session",
            AsyncMock(
                return_value=("sessions/123", {"status": "success", "pr_url": "http://pr/1"})
            ),
        ),
        patch("src.services.coder_usecase.workspace_lock", new_callable=AsyncMock),
    ):
        result = await usecase.execute(state)
        assert result["status"] == FlowStatus.READY_FOR_SELF_CRITIC


@pytest.mark.asyncio
async def test_session_reuse_ready_for_audit() -> None:
    """Verify that READY_FOR_AUDIT status triggers session reuse."""
    mock_jules = MagicMock()
    mock_jules.get_session_state = AsyncMock(return_value="COMPLETED")
    mock_jules.continue_session = AsyncMock(
        return_value={"status": "success", "pr_url": "http://pr/1"}
    )
    mock_jules.wait_for_completion = AsyncMock()
    mock_jules.get_latest_branch_commit = AsyncMock()

    usecase = CoderUseCase(mock_jules)
    state = CycleState(cycle_id="01")
    state.status = FlowStatus.READY_FOR_AUDIT
    state.session.jules_session_name = "sessions/123"
    state.audit.audit_result = MagicMock(feedback=["Loopback feedback"])

    cycle_manifest = CycleManifest(id="01", jules_session_id="sessions/123")
    mock_mgr = MagicMock()
    mock_mgr.get_cycle.return_value = cycle_manifest

    with (
        patch("src.services.coder_usecase.StateManager", return_value=mock_mgr),
        patch("src.services.coder_usecase.workspace_lock", new_callable=AsyncMock),
    ):
        result = await usecase.execute(state)

        assert mock_jules.continue_session.called
        # Now it should return READY_FOR_SELF_CRITIC because it's no longer considered an 'initial pr'
        assert result["status"] == FlowStatus.READY_FOR_SELF_CRITIC


@pytest.mark.asyncio
async def test_session_reuse_ready_for_final_critic() -> None:
    """Verify that READY_FOR_FINAL_CRITIC status triggers session reuse."""
    mock_jules = MagicMock()
    mock_jules.get_session_state = AsyncMock(return_value="COMPLETED")
    mock_jules.continue_session = AsyncMock(
        return_value={"status": "success", "pr_url": "http://pr/2"}
    )
    mock_jules.wait_for_completion = AsyncMock()
    mock_jules.get_latest_branch_commit = AsyncMock()

    usecase = CoderUseCase(mock_jules)
    state = CycleState(cycle_id="01")
    state.status = FlowStatus.READY_FOR_FINAL_CRITIC
    state.session.jules_session_name = "sessions/123"
    state.audit.audit_result = MagicMock(feedback=["Final Polish Feedback"])

    cycle_manifest = CycleManifest(id="01", jules_session_id="sessions/123")
    mock_mgr = MagicMock()
    mock_mgr.get_cycle.return_value = cycle_manifest

    with (
        patch("src.services.coder_usecase.StateManager", return_value=mock_mgr),
        patch("src.services.coder_usecase.workspace_lock", new_callable=AsyncMock),
    ):
        result = await usecase.execute(state)

        assert mock_jules.continue_session.called
        assert result["status"] == FlowStatus.COMPLETED
