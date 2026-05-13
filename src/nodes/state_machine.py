from typing import Any, NamedTuple

from src.enums import FlowStatus, WorkPhase
from src.utils import logger


class RouteKey(NamedTuple):
    phase: WorkPhase
    status: FlowStatus | str | None


class RouterEngine:
    """
    Declarative routing engine to replace nested conditional logic.
    Defines transitions as a mapping of (Phase, Status) -> NextNode.
    """

    def __init__(self) -> None:
        # Table for route_sandbox_evaluate
        self.sandbox_map: dict[RouteKey, str] = {
            # Initial Coder Phase (can be INIT or CODER depending on node state update)
            RouteKey(WorkPhase.INIT, FlowStatus.READY_FOR_SELF_CRITIC): "self_critic_node",
            RouteKey(WorkPhase.CODER, FlowStatus.READY_FOR_SELF_CRITIC): "self_critic_node",
            RouteKey(WorkPhase.CODER, FlowStatus.TDD_FAILED): "impl_coder_node",
            # Self Critic Phase
            RouteKey(WorkPhase.SELF_CRITIC, FlowStatus.READY_FOR_AUDIT): "auditor",
            RouteKey(WorkPhase.SELF_CRITIC, FlowStatus.COMPLETED): "auditor",
            # ↓ Self-Critic 中に Mechanical Block が落ちた場合は Coder に差し戻す
            RouteKey(WorkPhase.SELF_CRITIC, FlowStatus.TDD_FAILED): "impl_coder_node",
            # Audit / Feedback Loop
            RouteKey(WorkPhase.AUDIT, FlowStatus.READY_FOR_AUDIT): "auditor",
            RouteKey(WorkPhase.AUDIT, FlowStatus.RETRY_FIX): "impl_coder_node",
            RouteKey(WorkPhase.AUDIT, FlowStatus.APPROVED): "refactor_node",
            RouteKey(WorkPhase.AUDIT, FlowStatus.COMPLETED): "refactor_node",
            # Refactoring Phase
            RouteKey(WorkPhase.REFACTORING, FlowStatus.READY_FOR_FINAL_CRITIC): "final_critic",
            RouteKey(WorkPhase.REFACTORING, FlowStatus.POST_AUDIT_REFACTOR): "final_critic",
            # Final Critic Phase
            RouteKey(WorkPhase.FINAL_CRITIC, FlowStatus.COMPLETED): "approve",
            # Terminal / Failure States
            RouteKey(WorkPhase.CODER, FlowStatus.FAILED): "failed",
            RouteKey(WorkPhase.AUDIT, FlowStatus.FAILED): "failed",
        }

    def resolve_sandbox_route(  # noqa: C901, PLR0911
        self, phase: WorkPhase, status: Any, tdd_phase: str | None = None
    ) -> str:
        """Resolves the next node after sandbox evaluation."""

        # 1. Handle TDD Red Phase Special Routing
        if tdd_phase == "red":
            if status == FlowStatus.TDD_FAILED:
                return "impl_coder_node"
            if status == FlowStatus.READY_FOR_AUDIT:
                return "test_coder_node"
            if status == FlowStatus.FAILED:
                return "failed"

        # 2. Lookup in table
        key = RouteKey(phase, status)
        if key in self.sandbox_map:
            next_node = self.sandbox_map[key]
            logger.info(f"[ROUTER] Resolved {key} -> {next_node}")
            return next_node

        # 3. Fallback logic for complex/unmapped cases
        logger.warning(f"[ROUTER] Unmapped state: phase={phase}, status={status}. Using fallback.")

        if status == FlowStatus.FAILED:
            return "failed"

        if phase == WorkPhase.CODER:
            return "self_critic_node"
        if phase == WorkPhase.SELF_CRITIC:
            return "auditor"
        if phase == WorkPhase.AUDIT:
            return "auditor"
        if phase == WorkPhase.REFACTORING:
            return "final_critic"
        if phase == WorkPhase.FINAL_CRITIC:
            return "approve"

        return "impl_coder_node"

    def resolve_committee_route(self, phase: WorkPhase, status: Any) -> str:
        """Resolves the next node after committee review."""
        if status == FlowStatus.NEXT_AUDITOR:
            return "next_auditor"

        if phase == WorkPhase.REFACTORING or status == FlowStatus.POST_AUDIT_REFACTOR:
            return "refactor_node"

        if phase == WorkPhase.FINAL_CRITIC or status == FlowStatus.READY_FOR_AUDIT:
            return "final_critic"

        if status == FlowStatus.WAIT_FOR_JULES_COMPLETION:
            return "impl_coder_node"

        # Default: send back to coder for a fix
        return "impl_coder_node"
