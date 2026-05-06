from enum import StrEnum


class WorkPhase(StrEnum):
    INIT = "init"
    ARCHITECT = "architect"
    ARCHITECT_DONE = "architect_done"
    CODER = "coder"
    SELF_CRITIC = "self_critic"
    AUDIT = "audit"
    REFACTORING = "refactoring"
    FINAL_CRITIC = "final_critic"
    QA = "qa"


class FlowStatus(StrEnum):
    # Common
    START = "start"
    FAILED = "failed"
    COMPLETED = "completed"
    END = "end"

    # Architect
    ARCHITECT_COMPLETED = "architect_completed"
    ARCHITECT_FAILED = "architect_failed"
    ARCHITECT_SESSION_COMPLETED = "architect_session_completed"

    # Coder / Session
    READY_FOR_AUDIT = "ready_for_audit"
    CODER_RETRY = "coder_retry"
    RETRY_FIX = "retry_fix"
    READY_FOR_SELF_CRITIC = "ready_for_self_critic"
    READY_FOR_FINAL_CRITIC = "ready_for_final_critic"
    WAIT_FOR_JULES_COMPLETION = "wait_for_jules_completion"

    # Auditor / Committee
    APPROVED = "approved"
    REJECTED = "rejected"
    WAITING_FOR_JULES = "waiting_for_jules"
    NEXT_AUDITOR = "next_auditor"
    CYCLE_APPROVED = "cycle_approved"
    POST_AUDIT_REFACTOR = "post_audit_refactor"

    # UAT & Refactor
    UAT_FAILED = "uat_failed"
    TDD_FAILED = "tdd_failed"
    TDD_RED_PASSED = "tdd_red_passed"
    REQUIRES_PIVOT = "requires_pivot"
    START_REFACTOR = "start_refactor"
    CONFLICT_DETECTED = "conflict_detected"
    CONFLICT_RESOLVED = "conflict_resolved"

    # QA
    MAX_RETRIES = "max_retries"
    ARCHITECT_CRITIC_REJECTED = "architect_critic_rejected"
