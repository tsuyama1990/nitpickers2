from src.config import settings
from src.domain_models import AuditResult
from src.enums import FlowStatus, WorkPhase
from src.nodes.routers import route_auditor, route_final_critic, route_sandbox_evaluate
from src.state import CycleState


def test_route_sandbox_evaluate_failed() -> None:
    state = CycleState(cycle_id="01")
    state.status = FlowStatus.FAILED
    assert route_sandbox_evaluate(state) == "failed"


def test_route_sandbox_evaluate_refactoring() -> None:
    state = CycleState(cycle_id="01")
    state.status = FlowStatus.READY_FOR_FINAL_CRITIC
    state.current_phase = WorkPhase.REFACTORING
    assert route_sandbox_evaluate(state) == "final_critic"


def test_route_sandbox_evaluate_auditor() -> None:
    state = CycleState(cycle_id="01")
    state.status = FlowStatus.READY_FOR_AUDIT
    state.current_phase = WorkPhase.AUDIT
    assert route_sandbox_evaluate(state) == "auditor"


def test_route_auditor_reject() -> None:
    state = CycleState(cycle_id="01")
    state.audit.audit_result = AuditResult(is_approved=False, status="Reject")
    route = route_auditor(state)
    assert route == "reject"


def test_route_auditor_reject_fallback() -> None:
    pass  # Obsolete logic


def test_route_auditor_approve_next() -> None:
    state = CycleState(cycle_id="01")
    state.audit.audit_result = AuditResult(is_approved=True, status="Approve")
    state.committee.current_auditor_index = 1
    route = route_auditor(state)
    assert route == "next_auditor"


def test_route_auditor_approve_pass_all() -> None:
    state = CycleState(cycle_id="01")
    state.audit.audit_result = AuditResult(is_approved=True, status="Approve")
    state.committee.current_auditor_index = settings.NUM_AUDITORS
    route = route_auditor(state)
    assert route == "pass_all"


def test_route_final_critic() -> None:
    state = CycleState(cycle_id="01")

    state.status = FlowStatus.COMPLETED
    assert route_final_critic(state) == "approve"

    state.status = FlowStatus.FAILED
    assert route_final_critic(state) == "reject"
