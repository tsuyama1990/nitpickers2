from src.enums import FlowStatus
from src.nodes.routers import route_architect_critic
from src.state import CycleState


def test_route_architect_critic() -> None:
    state_approved = CycleState(cycle_id="01", status=FlowStatus.ARCHITECT_COMPLETED)
    assert route_architect_critic(state_approved) == "end"

    state_rejected = CycleState(cycle_id="01", status=FlowStatus.ARCHITECT_CRITIC_REJECTED)
    assert route_architect_critic(state_rejected) == "architect_session"

    state_failed = CycleState(cycle_id="01", status=FlowStatus.ARCHITECT_FAILED)
    assert route_architect_critic(state_failed) == "end"
