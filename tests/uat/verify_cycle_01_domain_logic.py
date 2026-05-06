import sys
from pathlib import Path

import marimo

from src.enums import WorkPhase

# Provide required modules available to Marimo when executing this standalone snippet
try:
    # Try local import
    from src.config import settings
    from src.domain_models.review import AuditResult
    from src.enums import FlowStatus
    from src.nodes.routers import route_auditor, route_final_critic, route_sandbox_evaluate
    from src.state import AuditState, CycleState
except ImportError:
    # Try dynamic sys.path append
    sys.path.append(str(Path(__file__).parent.parent.parent))
    from src.config import settings
    from src.domain_models.review import AuditResult
    from src.enums import FlowStatus
    from src.nodes.routers import route_auditor, route_final_critic, route_sandbox_evaluate
    from src.state import AuditState, CycleState

app = marimo.App()


@app.cell
def test_ux_flow_cycle_01() -> None:
    # Verify Coder_Phase_01 Happy Path (Initial Coder -> Sandbox -> 3 Auditors -> Refactor -> Sandbox -> Final Critic)

    state = CycleState(cycle_id="01")

    # Mock Sandbox evaluate
    state.status = FlowStatus.READY_FOR_AUDIT
    sandbox_route = route_sandbox_evaluate(state)
    assert sandbox_route == "auditor"

    # Mock 3 Auditors
    audit_res_approved = AuditResult(is_approved=True)
    state.audit = AuditState(audit_result=audit_res_approved)
    for expected_index in range(1, settings.NUM_AUDITORS + 1):
        state.current_auditor_index = expected_index
        next_route = route_auditor(state)
        if expected_index < settings.NUM_AUDITORS:
            assert next_route == "next_auditor"
        else:
            assert next_route == "pass_all"

    # Mock Refactor
    state.current_phase = WorkPhase.REFACTORING
    state.status = FlowStatus.READY_FOR_AUDIT

    # Mock Sandbox evaluate post-refactor
    sandbox_route = route_sandbox_evaluate(state)
    assert sandbox_route == "final_critic"

    # Mock Final Critic
    state.status = FlowStatus.COMPLETED
    final_critic_route = route_final_critic(state)
    assert final_critic_route == "approve"


@app.cell
def test_ux_flow_cycle_02_rejection() -> None:
    # Verify Coder_Phase_02 Rejection loop

    state = CycleState(cycle_id="01")
    state.status = FlowStatus.READY_FOR_AUDIT

    # Audit fails
    audit_res_rejected = AuditResult(is_approved=False)
    state.audit = AuditState(audit_result=audit_res_rejected)

    # First attempt fails
    assert state.committee.audit_attempt_count == 0
    route = route_auditor(state)
    assert route == "reject"
    assert state.committee.audit_attempt_count == 1

    # Coder retries, sandbox fails
    state.status = FlowStatus.TDD_FAILED
    sandbox_route = route_sandbox_evaluate(state)
    assert sandbox_route == "failed"


if __name__ == "__main__":
    app.run()
