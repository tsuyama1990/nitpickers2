from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.runnables import RunnableConfig

from src.enums import FlowStatus, WorkPhase
from src.graph import GraphBuilder
from src.services.git_ops import GitManager
from src.services.jules_client import JulesClient
from src.state import CycleState


@pytest.fixture
def mock_services() -> MagicMock:
    services = MagicMock()
    # Mock GitManager to avoid real git operations
    services.git_manager = MagicMock(spec=GitManager)
    services.git_manager.get_current_branch.return_value = "main"
    services.git_manager.create_worktree = AsyncMock(return_value=Path("/tmp/dummy_worktree"))  # noqa: S108

    # Mock SandboxRunner
    services.sandbox = AsyncMock()
    services.sandbox.run_checks.return_value = (0, "Success", "")

    return services


@pytest.mark.asyncio
async def test_full_coder_pipeline_success_flow(
    mock_services: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Integration test simulating a complete successful cycle:
    Initial -> Self-Critic -> Auditor Reject -> Feedback Fix -> Auditor Approve -> Refactor -> Final Critic -> Approve
    """
    # 1. Setup specialized Jules Mock to simulate state transitions
    jules_mock = MagicMock(spec=JulesClient)
    jules_mock.session_id = "test_session_id"

    # State-machine for Jules session status
    # Turn 1: Initial Coder -> COMPLETED
    # Turn 2: Self-Critic -> COMPLETED
    # Turn 3: Feedback Fix -> COMPLETED
    # Turn 4: Refactor -> COMPLETED
    # Turn 5: Final Critic -> COMPLETED
    status_responses = ["COMPLETED", "COMPLETED", "COMPLETED", "COMPLETED", "COMPLETED"]
    jules_mock.wait_for_completion = AsyncMock(side_effect=status_responses)
    jules_mock.continue_session = AsyncMock(return_value=None)
    jules_mock.start_session = AsyncMock(return_value="test_session_id")
    from src.services.audit_orchestrator import AuditOrchestrator

    # 2. Setup Auditor Mock
    # Turn 1: Reject
    # Turn 2: Approve
    audit_orchestrator_mock = MagicMock(spec=AuditOrchestrator)
    audit_orchestrator_mock.run_committee_review = AsyncMock(side_effect=["rejected", "approved"])

    # 3. Build the Graph
    with patch("src.graph.CycleNodes") as mock_cycle_nodes_class:
        # We want to use the REAL nodes but with our mocked services
        from src.graph_nodes import CycleNodes

        real_nodes = CycleNodes(
            jules_client=jules_mock,
            git_manager=mock_services.git_manager,
        )
        mock_cycle_nodes_class.return_value = real_nodes

        builder = GraphBuilder(mock_services, jules=jules_mock)
        graph = builder.build_coder_graph()

    # 4. Initial State
    from src.state import SessionPersistenceState

    initial_state = CycleState(
        cycle_id="01",
        status=FlowStatus.START,
        current_phase=WorkPhase.INIT,
        session=SessionPersistenceState(
            project_session_id="test_integration",
            feature_branch="feat/test",
            integration_branch="main",
        ),
    )

    # 5. Run Graph
    config = RunnableConfig(configurable={"thread_id": "test_thread", "cycle_id": "01"})

    # We use a simple history list to track phase transitions
    phase_history = []

    async def track_phases(state: dict[str, Any]) -> dict[str, Any]:
        phase_history.append(state.get("current_phase"))
        return state

    # Actually, we can just inspect the final_state since nodes update it
    final_state = await graph.ainvoke(initial_state, config=config)

    # 6. Assertions
    # Check that we reached the end successfully
    assert final_state["status"] == FlowStatus.COMPLETED

    # Verify the Auditor was called exactly twice (once rejected, once approved)
    assert audit_orchestrator_mock.run_committee_review.call_count == 2

    # Verify Jules was called for:
    # 1. Initial Implementation
    # 2. Self-Critic
    # 3. Feedback Fix (after audit rejection)
    # 4. Refactor (after audit approval)
    # 5. Final Critic
    # Some turns might share a jules.wait_for_completion call if logic is optimized,
    # but based on current nodes:
    assert jules_mock.wait_for_completion.call_count >= 5

    # Verify that the flow went through the Feedback Fix loop
    # If the auditor rejected, the phase should have been at some point RETRY_FIX or AUDIT
    # We can check that the last_processed_commit was updated multiple times
    assert jules_mock.continue_session.call_count >= 4

    from src.utils import logger

    logger.info("[PASSED] Master Integration Test Baseline Verified.")
    logger.info(
        f"Turns: Auditor={audit_orchestrator_mock.run_committee_review.call_count}, Jules={jules_mock.wait_for_completion.call_count}"
    )
