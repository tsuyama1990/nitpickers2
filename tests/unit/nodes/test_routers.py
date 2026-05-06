from typing import Any

from src.config import settings
from src.domain_models import AuditResult
from src.enums import FlowStatus
from src.nodes.routers import route_auditor, route_final_critic, route_sandbox_evaluate
from src.state import AuditState, CommitteeState, CycleState


def test_route_sandbox_evaluate_failed() -> None:
    state = CycleState(cycle_id="01", status=FlowStatus.FAILED)
    # the spec says: "If state.get("sandbox_status") == "failed", return "failed"."
    # the current implementation returns "impl_coder_node" for GREEN phase
    # Wait, the prompt says sandbox_status=="failed", let's simulate this state.
    state.status = FlowStatus.FAILED
    assert route_sandbox_evaluate(state) == "failed"


def test_route_sandbox_evaluate_refactoring() -> None:
    state = CycleState(
        cycle_id="02",
        status=FlowStatus.READY_FOR_AUDIT,
        committee=CommitteeState(),
    )
    # should return final_critic
    assert route_sandbox_evaluate(state) == "final_critic"


def test_route_sandbox_evaluate_auditor() -> None:
    state = CycleState(
        cycle_id="03",
        status=FlowStatus.READY_FOR_AUDIT,
        committee=CommitteeState(),
    )
    assert route_sandbox_evaluate(state) == "auditor"


def test_route_auditor_reject() -> None:
    state = CycleState(
        cycle_id="04",
        audit=AuditState(
            audit_result=AuditResult(
                is_approved=False,
            )
        ),
    )
    state.committee.audit_attempt_count = 0
    # when rejected, should return "reject"
    assert route_auditor(state) == "reject"


def test_route_auditor_pass_next_auditor() -> None:
    state = CycleState(
        cycle_id="05",
        audit=AuditState(
            audit_result=AuditResult(
                is_approved=True,
            )
        ),
        committee=CommitteeState(current_auditor_index=1, audit_attempt_count=1),
    )
    # when passed, should return next_auditor if not last
    res = route_auditor(state)
    assert res == "next_auditor"


def test_route_auditor_pass_all() -> None:
    # Need to simulate index going beyond NUM_AUDITORS
    # if NUM_AUDITORS is e.g. 3, current=3 -> increments to 4, which is > 3 -> pass_all
    state = CycleState(
        cycle_id="06",
        audit=AuditState(
            audit_result=AuditResult(
                is_approved=True,
            )
        ),
        committee=CommitteeState(current_auditor_index=getattr(settings, "NUM_AUDITORS", 3)),
    )
    res = route_auditor(state)
    assert res == "pass_all"


def test_route_final_critic_approve() -> None:
    state = CycleState(cycle_id="07", status=FlowStatus.COMPLETED)
    assert route_final_critic(state) == "approve"


def test_route_final_critic_reject() -> None:
    state = CycleState(cycle_id="08", status=FlowStatus.FAILED)
    assert route_final_critic(state) == "reject"


def test_route_sandbox_evaluate_failed_sandbox_status() -> None:
    # Adding specific test for the spec logic "state.get('sandbox_status') == 'failed'"
    state = CycleState(cycle_id="09", status=FlowStatus.FAILED)
    # Monkeypatch get to simulate sandbox_status
    original_get = state.get

    def mock_get(key: str, default: Any = None) -> Any:
        if key == "sandbox_status":
            return "failed"
        return original_get(key, default)

    object.__setattr__(state, "get", mock_get)
    assert route_sandbox_evaluate(state) == "failed"
