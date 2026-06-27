from typing import Any

from src.enums import FlowStatus
from src.state import CycleState


def check_coder_outcome(state: CycleState) -> str:
    status = getattr(state, "status", None)
    from src.utils import logger

    logger.info(f"[ROUTER] check_coder_outcome: status={status}")

    if status in {FlowStatus.FAILED, FlowStatus.ARCHITECT_FAILED}:
        return str(FlowStatus.FAILED.value)

    if status == FlowStatus.COMPLETED:
        return str(FlowStatus.COMPLETED.value)

    if status == FlowStatus.CODER_RETRY:
        return "impl_coder_node"

    logger.info(f"[ROUTER] check_coder_outcome -> self_critic_node (status={status})")
    return "self_critic_node"


def route_committee(state: CycleState) -> str:
    from src.enums import WorkPhase

    status = getattr(state, "status", None)
    phase = getattr(state, "current_phase", None)

    if status == FlowStatus.NEXT_AUDITOR:
        return "next_auditor"

    if status == FlowStatus.COMPLETED:
        return "final_critic"

    if phase == WorkPhase.REFACTORING or status == FlowStatus.POST_AUDIT_REFACTOR:
        return "refactor_node"

    if phase == WorkPhase.FINAL_CRITIC or status == FlowStatus.READY_FOR_AUDIT:
        return "final_critic"

    if status == FlowStatus.WAIT_FOR_JULES_COMPLETION:
        return "impl_coder_node"

    return "impl_coder_node"


def route_qa(state: CycleState) -> str:
    """Routes from uat_evaluate: UAT_FAILED → qa_auditor, else → ux_auditor."""
    status = getattr(state, "status", None)
    if status == FlowStatus.UAT_FAILED:
        return "qa_auditor"
    return "ux_auditor"


def route_architect_session(state: CycleState) -> str:
    status = getattr(state, "status", None)
    if status == FlowStatus.ARCHITECT_SESSION_COMPLETED:
        return "architect_critic"
    return "end"


def route_architect_critic(state: CycleState) -> str:
    status = getattr(state, "status", None)
    if status == FlowStatus.ARCHITECT_CRITIC_REJECTED:
        return "architect_session"
    if status == FlowStatus.ARCHITECT_COMPLETED:
        return "end"
    if status == FlowStatus.ARCHITECT_FAILED:
        return "end"
    return "end"


def route_merge(state: Any) -> str:
    status = getattr(state, "status", None)
    if not status and hasattr(state, "get"):
        status = state.get("status")

    if (
        status == "conflict"
        or getattr(state, "conflict_status", None) == "conflict_detected"
        or (hasattr(state, "get") and state.get("conflict_status") == "conflict_detected")
    ):
        return "conflict"
    return "success"
