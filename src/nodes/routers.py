from typing import Any

from src.config import settings
from src.enums import FlowStatus
from src.state import CycleState


def check_coder_outcome(state: CycleState) -> str:
    status = getattr(state, "status", None)
    current_phase = getattr(state, "current_phase", None)
    from src.utils import logger

    logger.info(f"[ROUTER] check_coder_outcome: status={status}, phase={current_phase}")

    if status in {FlowStatus.FAILED, FlowStatus.ARCHITECT_FAILED}:
        return str(FlowStatus.FAILED.value)

    if status == FlowStatus.COMPLETED:
        return str(FlowStatus.COMPLETED.value)

    if status == FlowStatus.CODER_RETRY:
        return "impl_coder_node"

    logger.info(
        f"[ROUTER] check_coder_outcome -> default sandbox (status={status}, phase={current_phase})"
    )
    return settings.node_sandbox_evaluate


def route_sandbox_evaluate(state: CycleState) -> str:  # noqa: PLR0911, C901
    status = getattr(state, "status", None)
    current_phase = getattr(state, "current_phase", None)
    from src.enums import WorkPhase
    from src.utils import logger

    logger.info(f"[ROUTER] route_sandbox_evaluate: status={status}, phase={current_phase}")

    if getattr(state.test, "tdd_phase", None) == "red":
        if status == FlowStatus.FAILED:
            return "failed"
        if status == FlowStatus.TDD_FAILED:
            return "impl_coder_node"
        if status == FlowStatus.READY_FOR_AUDIT:
            return "test_coder_node"

    if status == FlowStatus.FAILED:
        logger.info("[ROUTER] route_sandbox_evaluate -> failed (status=FAILED)")
        return "failed"

    if status == FlowStatus.TDD_FAILED:
        logger.info("[ROUTER] route_sandbox_evaluate -> impl_coder_node (status=TDD_FAILED)")
        return "impl_coder_node"

    if status == FlowStatus.READY_FOR_SELF_CRITIC:
        logger.info(
            "[ROUTER] route_sandbox_evaluate -> self_critic_node (status=READY_FOR_SELF_CRITIC)"
        )
        return "self_critic_node"

    if status == FlowStatus.READY_FOR_AUDIT:
        logger.info("[ROUTER] route_sandbox_evaluate -> auditor (status=READY_FOR_AUDIT)")
        return "auditor"

    if status == FlowStatus.READY_FOR_FINAL_CRITIC:
        logger.info(
            "[ROUTER] route_sandbox_evaluate -> final_critic (status=READY_FOR_FINAL_CRITIC)"
        )
        return "final_critic"

    if current_phase == WorkPhase.CODER:
        logger.info("[ROUTER] route_sandbox_evaluate -> self_critic_node (phase=CODER)")
        return "self_critic_node"

    if current_phase == WorkPhase.SELF_CRITIC:
        logger.info("[ROUTER] route_sandbox_evaluate -> auditor (phase=SELF_CRITIC)")
        return "auditor"

    if current_phase == WorkPhase.AUDIT:
        logger.info("[ROUTER] route_sandbox_evaluate -> auditor (phase=AUDIT)")
        return "auditor"

    if current_phase == WorkPhase.REFACTORING:
        logger.info("[ROUTER] route_sandbox_evaluate -> final_critic (phase=REFACTORING)")
        return "final_critic"

    if current_phase == WorkPhase.FINAL_CRITIC:
        logger.info("[ROUTER] route_sandbox_evaluate -> approve (phase=FINAL_CRITIC)")
        return "approve"

    if status == FlowStatus.WAITING_FOR_JULES:
        logger.info("[ROUTER] route_sandbox_evaluate -> impl_coder_node (status=WAITING_FOR_JULES)")
        return "impl_coder_node"

    logger.info(
        f"[ROUTER] route_sandbox_evaluate -> default impl_coder_node (status={status}, phase={current_phase})"
    )
    return "impl_coder_node"


def route_auditor(state: CycleState) -> str:
    is_approved = False
    if state.audit.audit_result is not None:
        is_approved = state.audit.audit_result.is_approved

    if not is_approved:
        return "reject"

    # Note: Committee index increment is now handled in CommitteeUseCase or AuditorUseCase
    if state.committee.current_auditor_index >= settings.NUM_AUDITORS:
        return "pass_all"

    return "next_auditor"


def route_committee(state: CycleState) -> str:
    from src.enums import WorkPhase

    """Routes after CommitteeUseCase executes, based on the returned status."""
    status = getattr(state, "status", None)
    phase = getattr(state, "current_phase", None)

    if status == FlowStatus.NEXT_AUDITOR:
        return "next_auditor"

    if phase == WorkPhase.REFACTORING or status == FlowStatus.POST_AUDIT_REFACTOR:
        return "refactor_node"

    if phase == WorkPhase.FINAL_CRITIC or status == FlowStatus.READY_FOR_AUDIT:
        return "final_critic"

    if status == FlowStatus.WAIT_FOR_JULES_COMPLETION:
        return "impl_coder_node"

    # Default: send back to coder for a fix (RETRY_FIX or any other status)
    return "impl_coder_node"


def route_final_critic(state: CycleState) -> str:
    status = getattr(state, "status", None)
    if status == FlowStatus.COMPLETED:
        return "approve"
    return "reject"


def route_qa(state: CycleState) -> str:
    status = getattr(state, "status", None)
    if status == FlowStatus.APPROVED:
        return "end"
    if status == FlowStatus.REJECTED:
        return "retry_fix"
    return "failed"


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


def route_global_sandbox(state: Any) -> str:
    status = getattr(state, "status", None)
    if not status and hasattr(state, "get"):
        status = state.get("status")
    if status in ("failed", "tdd_failed"):
        return "failed"
    return "pass"
