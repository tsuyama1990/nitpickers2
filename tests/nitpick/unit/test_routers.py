from src.config import settings
from src.domain_models.review import AuditResult
from src.enums import FlowStatus, WorkPhase
from src.nodes.routers import (
    route_architect_critic,
    route_auditor,
    route_final_critic,
    route_qa,
    route_sandbox_evaluate,
)
from src.state import AuditState, CycleState


def test_route_sandbox_evaluate() -> None:
    # Test RED phase failures go to impl_coder_node
    state = CycleState(cycle_id="01", status=FlowStatus.TDD_FAILED)
    state.test.tdd_phase = "red"
    assert route_sandbox_evaluate(state) == "impl_coder_node"

    # Test RED phase success (incorrectly passes) goes back to test_coder_node
    state = CycleState(cycle_id="01", status=FlowStatus.READY_FOR_AUDIT)
    state.test.tdd_phase = "red"
    assert route_sandbox_evaluate(state) == "test_coder_node"

    # Test GREEN phase TDD_FAILED goes to impl_coder_node
    state = CycleState(cycle_id="01", status=FlowStatus.TDD_FAILED)
    state.test.tdd_phase = "green"
    assert route_sandbox_evaluate(state) == "impl_coder_node"

    # Test GREEN phase READY_FOR_AUDIT, not refactoring
    state = CycleState(cycle_id="01", status=FlowStatus.READY_FOR_AUDIT)
    state.test.tdd_phase = "green"
    state.current_phase = WorkPhase.AUDIT
    assert route_sandbox_evaluate(state) == "auditor"

    # Test GREEN phase READY_FOR_AUDIT, is refactoring
    state.current_phase = WorkPhase.REFACTORING
    assert route_sandbox_evaluate(state) == "final_critic"

    # Test GREEN phase fallback
    state = CycleState(cycle_id="01", status=FlowStatus.COMPLETED)
    state.test.tdd_phase = "green"
    assert route_sandbox_evaluate(state) == "impl_coder_node"


def test_route_auditor() -> None:
    # Test rejected by None audit result
    state = CycleState(cycle_id="01")
    assert route_auditor(state) == "reject"

    # Test rejected by False is_approved
    audit_res = AuditResult(is_approved=False)
    state.audit = AuditState(audit_result=audit_res)
    assert route_auditor(state) == "reject"

    # Test approved, next_auditor
    audit_res_approved = AuditResult(is_approved=True)
    state.audit = AuditState(audit_result=audit_res_approved)
    state.committee.current_auditor_index = 1
    assert route_auditor(state) == "next_auditor"

    # Test approved, pass_all
    state.committee.current_auditor_index = settings.NUM_AUDITORS
    assert route_auditor(state) == "pass_all"


def test_route_final_critic() -> None:
    # Test coder retry
    state = CycleState(cycle_id="01", status=FlowStatus.CODER_RETRY)
    assert route_final_critic(state) == "reject"

    # Test approved
    state = CycleState(cycle_id="01", status=FlowStatus.COMPLETED)
    assert route_final_critic(state) == "approve"

    # Test fallback
    state = CycleState(cycle_id="01", status=FlowStatus.FAILED)
    assert route_final_critic(state) == "reject"


def test_route_qa() -> None:
    # Test approved
    state = CycleState(cycle_id="01", status=FlowStatus.APPROVED)
    assert route_qa(state) == "end"

    # Test rejected
    state = CycleState(cycle_id="01", status=FlowStatus.REJECTED)
    assert route_qa(state) == "retry_fix"

    # Test fallback
    state = CycleState(cycle_id="01", status=FlowStatus.FAILED)
    assert route_qa(state) == "failed"


def test_route_architect_critic() -> None:
    # Test architect_completed
    state = CycleState(cycle_id="01", status=FlowStatus.ARCHITECT_COMPLETED)
    assert route_architect_critic(state) == "end"

    # Test architect_failed
    state = CycleState(cycle_id="01", status=FlowStatus.ARCHITECT_FAILED)
    assert route_architect_critic(state) == "end"

    # Test architect_critic_rejected
    state = CycleState(cycle_id="01", status=FlowStatus.ARCHITECT_CRITIC_REJECTED)
    assert route_architect_critic(state) == "architect_session"

    # Test fallback (any other status)
    state = CycleState(cycle_id="01")
    assert route_architect_critic(state) == "end"

    state = CycleState(cycle_id="01", status=FlowStatus.COMPLETED)
    assert route_architect_critic(state) == "end"
