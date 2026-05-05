from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CycleStatus = Literal[
    "planned",
    "in_progress",
    "in-progress",
    "review_fix",
    "completed",
    "failed",
    "ready_for_self_critic",
    "ready_for_final_critic",
    "ready_for_audit",
    "post_audit_refactor",
    "tdd_failed",
    "conflict_detected",
    "conflict_resolved",
    "coder_retry",
    "retry_fix",
    "wait_for_jules_completion",
    "approved",
    "rejected",
    "waiting_for_jules",
    "next_auditor",
    "cycle_approved",
    "start_refactor",
    "uat_failed",
    "tdd_red_passed",
    "requires_pivot",
]


class CycleManifest(BaseModel):
    """Manifest for a single development cycle."""

    model_config = ConfigDict(extra="forbid")

    id: str
    status: CycleStatus = "planned"
    branch_name: str | None = None
    # Resume-critical field
    jules_session_id: str | None = Field(default=None, description="Active AI session ID")
    current_iteration: int = 1
    pr_url: str | None = None
    last_error: str | None = None
    # DAG Scheduling
    depends_on: list[str] = Field(
        default_factory=list, description="IDs of cycles that must be completed before this one"
    )
    # Session restart tracking
    session_restart_count: int = Field(
        default=0, description="Number of session restarts attempted"
    )
    max_session_restarts: int = Field(default=4, description="Maximum allowed session restarts")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProjectManifest(BaseModel):
    """Root manifest for the entire project state."""

    model_config = ConfigDict(extra="forbid")

    project_session_id: str
    feature_branch: str | None = None  # Main development branch (feat/generate-architecture-*)
    integration_branch: str  # Final integration branch (for finalize-session)
    qa_session_id: str | None = Field(
        default=None, description="Active QA/Tutorial Generation Session ID"
    )
    cycles: list[CycleManifest] = Field(default_factory=list)
    unresolved_conflicts: list[dict[str, Any]] = Field(
        default_factory=list, description="Serialized ConflictRegistryItem logs"
    )
    last_updated: datetime = Field(default_factory=lambda: datetime.now(UTC))
