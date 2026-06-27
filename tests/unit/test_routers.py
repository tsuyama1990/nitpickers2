"""Unit tests for router functions in src/nodes/routers.py.

Tests each router function exhaustively across all branches.
"""

from typing import Any

from src.enums import FlowStatus, WorkPhase
from src.nodes.routers import (
    check_coder_outcome,
    route_architect_critic,
    route_architect_session,
    route_committee,
    route_merge,
    route_qa,
)
from src.state import CycleState, IntegrationState

# ---------------------------------------------------------------------------
#  check_coder_outcome
# ---------------------------------------------------------------------------

class TestCheckCoderOutcome:
    def test_failed_status(self) -> None:
        state = CycleState(cycle_id="01", status=FlowStatus.FAILED)
        assert check_coder_outcome(state) == FlowStatus.FAILED.value

    def test_architect_failed_status(self) -> None:
        state = CycleState(cycle_id="01", status=FlowStatus.ARCHITECT_FAILED)
        assert check_coder_outcome(state) == FlowStatus.FAILED.value

    def test_completed_status(self) -> None:
        state = CycleState(cycle_id="01", status=FlowStatus.COMPLETED)
        assert check_coder_outcome(state) == FlowStatus.COMPLETED.value

    def test_coder_retry_status(self) -> None:
        state = CycleState(cycle_id="01", status=FlowStatus.CODER_RETRY)
        assert check_coder_outcome(state) == "impl_coder_node"

    def test_default_to_self_critic(self) -> None:
        """When status is None (first invocation) or unexpected, route to self_critic_node."""
        state = CycleState(cycle_id="01")
        assert check_coder_outcome(state) == "self_critic_node"

    def test_start_status_defaults_to_self_critic(self) -> None:
        state = CycleState(cycle_id="01", status=FlowStatus.START)
        assert check_coder_outcome(state) == "self_critic_node"

    def test_ready_for_audit_defaults_to_self_critic(self) -> None:
        state = CycleState(cycle_id="01", status=FlowStatus.READY_FOR_AUDIT)
        assert check_coder_outcome(state) == "self_critic_node"


# ---------------------------------------------------------------------------
#  route_committee
# ---------------------------------------------------------------------------

class TestRouteCommittee:
    def test_next_auditor(self) -> None:
        state = CycleState(cycle_id="01", status=FlowStatus.NEXT_AUDITOR)
        assert route_committee(state) == "next_auditor"

    def test_completed(self) -> None:
        state = CycleState(cycle_id="01", status=FlowStatus.COMPLETED)
        assert route_committee(state) == "final_critic"

    def test_refactoring_phase(self) -> None:
        state = CycleState(
            cycle_id="01",
            current_phase=WorkPhase.REFACTORING,
        )
        assert route_committee(state) == "refactor_node"

    def test_post_audit_refactor_status(self) -> None:
        state = CycleState(
            cycle_id="01",
            status=FlowStatus.POST_AUDIT_REFACTOR,
        )
        assert route_committee(state) == "refactor_node"

    def test_final_critic_phase(self) -> None:
        state = CycleState(
            cycle_id="01",
            current_phase=WorkPhase.FINAL_CRITIC,
        )
        assert route_committee(state) == "final_critic"

    def test_ready_for_audit_with_final_critic_phase(self) -> None:
        """READY_FOR_AUDIT status with FINAL_CRITIC phase → final_critic."""
        state = CycleState(
            cycle_id="01",
            status=FlowStatus.READY_FOR_AUDIT,
            current_phase=WorkPhase.FINAL_CRITIC,
        )
        assert route_committee(state) == "final_critic"

    def test_wait_for_jules_completion(self) -> None:
        state = CycleState(
            cycle_id="01",
            status=FlowStatus.WAIT_FOR_JULES_COMPLETION,
        )
        assert route_committee(state) == "impl_coder_node"

    def test_default_impl_coder(self) -> None:
        """When status is None and phase is INIT, default to impl_coder_node."""
        state = CycleState(cycle_id="01")
        assert route_committee(state) == "impl_coder_node"

    def test_approved_status_defaults_to_impl_coder(self) -> None:
        """APPROVED status with no special phase → impl_coder_node (fallback)."""
        state = CycleState(cycle_id="01", status=FlowStatus.APPROVED)
        assert route_committee(state) == "impl_coder_node"


# ---------------------------------------------------------------------------
#  route_qa
# ---------------------------------------------------------------------------

class TestRouteQa:
    def test_uat_failed(self) -> None:
        state = CycleState(cycle_id="01", status=FlowStatus.UAT_FAILED)
        assert route_qa(state) == "qa_auditor"

    def test_default_to_ux_auditor(self) -> None:
        state = CycleState(cycle_id="01")
        assert route_qa(state) == "ux_auditor"

    def test_completed_status_routes_to_ux_auditor(self) -> None:
        state = CycleState(cycle_id="01", status=FlowStatus.COMPLETED)
        assert route_qa(state) == "ux_auditor"

    def test_start_status_routes_to_ux_auditor(self) -> None:
        state = CycleState(cycle_id="01", status=FlowStatus.START)
        assert route_qa(state) == "ux_auditor"


# ---------------------------------------------------------------------------
#  route_architect_session
# ---------------------------------------------------------------------------

class TestRouteArchitectSession:
    def test_session_completed(self) -> None:
        state = CycleState(
            cycle_id="01",
            status=FlowStatus.ARCHITECT_SESSION_COMPLETED,
        )
        assert route_architect_session(state) == "architect_critic"

    def test_default_to_end(self) -> None:
        state = CycleState(cycle_id="01")
        assert route_architect_session(state) == "end"

    def test_architect_completed_routes_to_end(self) -> None:
        state = CycleState(cycle_id="01", status=FlowStatus.ARCHITECT_COMPLETED)
        assert route_architect_session(state) == "end"

    def test_architect_failed_routes_to_end(self) -> None:
        state = CycleState(cycle_id="01", status=FlowStatus.ARCHITECT_FAILED)
        assert route_architect_session(state) == "end"


# ---------------------------------------------------------------------------
#  route_architect_critic
# ---------------------------------------------------------------------------

class TestRouteArchitectCritic:
    def test_critic_rejected(self) -> None:
        state = CycleState(
            cycle_id="01",
            status=FlowStatus.ARCHITECT_CRITIC_REJECTED,
        )
        assert route_architect_critic(state) == "architect_session"

    def test_architect_completed(self) -> None:
        state = CycleState(
            cycle_id="01",
            status=FlowStatus.ARCHITECT_COMPLETED,
        )
        assert route_architect_critic(state) == "end"

    def test_architect_failed(self) -> None:
        state = CycleState(
            cycle_id="01",
            status=FlowStatus.ARCHITECT_FAILED,
        )
        assert route_architect_critic(state) == "end"

    def test_default_to_end(self) -> None:
        state = CycleState(cycle_id="01")
        assert route_architect_critic(state) == "end"

    def test_session_completed_falls_to_end(self) -> None:
        """ARCHITECT_SESSION_COMPLETED on critic → end (no match)."""
        state = CycleState(
            cycle_id="01",
            status=FlowStatus.ARCHITECT_SESSION_COMPLETED,
        )
        assert route_architect_critic(state) == "end"


# ---------------------------------------------------------------------------
#  route_merge
# ---------------------------------------------------------------------------

class TestRouteMerge:
    def test_conflict_status(self) -> None:
        state = IntegrationState(status="conflict")
        assert route_merge(state) == "conflict"

    def test_conflict_detected_via_conflict_status(self) -> None:
        state = IntegrationState(conflict_status="conflict_detected")
        assert route_merge(state) == "conflict"

    def test_success_status(self) -> None:
        state = IntegrationState(status="success")
        assert route_merge(state) == "success"

    def test_none_status_defaults_to_success(self) -> None:
        state = IntegrationState()
        assert route_merge(state) == "success"

    def test_conflict_via_get_method(self) -> None:
        """Test dict-like access for langgraph's internal state representation."""
        state: dict[str, Any] = {"conflict_status": "conflict_detected"}
        assert route_merge(state) == "conflict"

    def test_flow_status_conflict_detected(self) -> None:
        """FlowStatus.CONFLICT_DETECTED via conflict_status field should also match."""
        state = IntegrationState(conflict_status="conflict_detected")
        assert route_merge(state) == "conflict"

    def test_dict_status_conflict(self) -> None:
        """Test plain dict with status='conflict'."""
        state: dict[str, Any] = {"status": "conflict"}
        assert route_merge(state) == "conflict"

    def test_dict_no_conflict(self) -> None:
        """Plain dict without conflict fields → success."""
        state: dict[str, Any] = {}
        assert route_merge(state) == "success"
