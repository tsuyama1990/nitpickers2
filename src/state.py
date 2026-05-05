from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.config import settings

from .domain_models import (
    AuditResult,
    ConflictRegistryItem,
    CyclePlan,
    FileOperation,
    FixPlanSchema,
    StructuralGateReport,
    UatAnalysis,
    UatExecutionState,
    UXAuditReport,
)
from .enums import FlowStatus, WorkPhase


class CommitteeState(BaseModel):
    current_auditor_index: int = Field(default=1, ge=1)
    current_auditor_review_count: int = Field(default=1, ge=1)
    iteration_count: int = Field(default=0, ge=0)
    is_refactoring: bool = Field(default=False)
    audit_attempt_count: int = Field(default=0, ge=0)
    fallback_count: int = Field(default=0, ge=0)
    anti_patterns_memory: list[str] = Field(default_factory=list)

    @field_validator("current_auditor_index")
    @classmethod
    def do_validate_auditor_index(cls, v: int) -> int:
        from src.state_validators import validate_auditor_index

        return validate_auditor_index(v)

    @field_validator("current_auditor_review_count")
    @classmethod
    def do_validate_review_count(cls, v: int) -> int:
        from src.state_validators import validate_review_count

        return validate_review_count(v)

    @field_validator("audit_attempt_count")
    @classmethod
    def do_validate_audit_attempt_count(cls, v: int) -> int:
        from src.state_validators import validate_audit_attempt_count

        return validate_audit_attempt_count(v)


class SessionPersistenceState(BaseModel):
    jules_session_name: str | None = None
    critic_retry_count: int = 0
    pr_url: str | None = None
    resume_mode: bool = False
    active_branch: str | None = None
    project_session_id: str | None = None
    feature_branch: str | None = None
    integration_branch: str | None = None
    is_session_finalized: bool = False
    branch_name: str | None = None
    last_processed_commit: str | None = None
    self_critic_completed: bool = False


class AuditState(BaseModel):
    audit_result: AuditResult | None = None
    audit_feedback: list[str] = Field(default_factory=list)
    audit_pass_count: int = 0
    audit_retries: int = 0
    audit_logs: str = ""
    last_audited_commit: str | None = None


class TestState(BaseModel):
    structural_report: StructuralGateReport | None = None
    test_logs: str = ""
    test_exit_code: int | None = None
    tdd_phase: Literal["red", "green"] | None = Field(default=None)


class UATState(BaseModel):
    uat_analysis: UatAnalysis | None = None
    uat_execution_state: UatExecutionState | None = None
    current_fix_plan: FixPlanSchema | None = None
    sandbox_artifacts: dict[str, Any] = Field(default_factory=dict)
    uat_retry_count: int = 0
    ux_audit_report: UXAuditReport | None = None


class ConfigurationState(BaseModel):
    planned_cycle_count: int | None = Field(
        default_factory=lambda: getattr(settings, "default_cycles_count", 5)
    )
    requested_cycle_count: int | None = None
    planned_cycles: list[str] = Field(default_factory=list)


class CycleState(BaseModel):
    """LangGraph state for the development cycle, composed from multiple state sub-models."""

    # Required fields
    cycle_id: str

    @field_validator("cycle_id")
    @classmethod
    def do_validate_cycle_id(cls, v: str) -> str:
        from src.state_validators import validate_cycle_id

        return validate_cycle_id(v)

    # Composed Sub-States (using default factories to auto-initialize)
    committee: CommitteeState = Field(default_factory=CommitteeState)
    session: SessionPersistenceState = Field(default_factory=SessionPersistenceState)
    audit: AuditState = Field(default_factory=AuditState)
    test: TestState = Field(default_factory=TestState)
    uat: UATState = Field(default_factory=UATState)
    config: ConfigurationState = Field(default_factory=ConfigurationState)

    # Core execution flags / Top Level fields that span across sub-domains naturally
    current_phase: WorkPhase = WorkPhase.INIT
    status: FlowStatus | None = None
    error: str | None = None
    conflict_status: FlowStatus | None = None
    concurrent_dependencies: list[str] = Field(default_factory=list)
    final_fix: bool = Field(default=False)
    qa_retry_count: int = 0
    branch_name: str | None = None

    # Legacy/Optional Fields - kept at root level for legacy backward compatibility easily
    sandbox_id: str | None = None
    plan: CyclePlan | None = None
    code_changes: list[FileOperation] = Field(default_factory=list)
    loop_count: int = 0
    correction_history: list[str] = Field(default_factory=list)
    dry_run: bool = False
    interactive: bool = False
    goal: str | None = None
    approved: bool | None = None
    coder_report: dict[str, Any] | None = None

    # Properties to maintain backward compatibility with legacy top-level accessors
    @property
    def current_auditor_index(self) -> int:
        return self.committee.current_auditor_index

    @current_auditor_index.setter
    def current_auditor_index(self, value: int) -> None:
        self.committee.current_auditor_index = value

    @property
    def current_auditor_review_count(self) -> int:
        return self.committee.current_auditor_review_count

    @current_auditor_review_count.setter
    def current_auditor_review_count(self, value: int) -> None:
        self.committee.current_auditor_review_count = value

    @property
    def iteration_count(self) -> int:
        return self.committee.iteration_count

    @iteration_count.setter
    def iteration_count(self, value: int) -> None:
        self.committee.iteration_count = value

    @property
    def is_refactoring(self) -> bool:
        return self.committee.is_refactoring

    @is_refactoring.setter
    def is_refactoring(self, value: bool) -> None:
        self.committee.is_refactoring = value

    @property
    def audit_attempt_count(self) -> int:
        return self.committee.audit_attempt_count

    @audit_attempt_count.setter
    def audit_attempt_count(self, value: int) -> None:
        self.committee.audit_attempt_count = value

    @property
    def project_session_id(self) -> str | None:
        return self.session.project_session_id

    @project_session_id.setter
    def project_session_id(self, value: str | None) -> None:
        self.session.project_session_id = value

    @property
    def critic_retry_count(self) -> int:
        return self.session.critic_retry_count

    @critic_retry_count.setter
    def critic_retry_count(self, value: int) -> None:
        self.session.critic_retry_count = value

    @property
    def jules_session_name(self) -> str | None:
        return self.session.jules_session_name

    @jules_session_name.setter
    def jules_session_name(self, value: str | None) -> None:
        self.session.jules_session_name = value

    @property
    def pr_url(self) -> str | None:
        return self.session.pr_url

    @pr_url.setter
    def pr_url(self, value: str | None) -> None:
        self.session.pr_url = value

    @property
    def resume_mode(self) -> bool:
        return self.session.resume_mode

    @resume_mode.setter
    def resume_mode(self, value: bool) -> None:
        self.session.resume_mode = value

    @property
    def feature_branch(self) -> str | None:
        return self.session.feature_branch

    @feature_branch.setter
    def feature_branch(self, value: str | None) -> None:
        self.session.feature_branch = value

    @feature_branch.setter
    def feature_branch(self, value: str | None) -> None:
        self.session.feature_branch = value

    @property
    def integration_branch(self) -> str | None:
        return self.session.integration_branch

    @integration_branch.setter
    def integration_branch(self, value: str | None) -> None:
        self.session.integration_branch = value

    @property
    def last_processed_commit(self) -> str | None:
        return self.session.last_processed_commit

    @last_processed_commit.setter
    def last_processed_commit(self, value: str | None) -> None:
        self.session.last_processed_commit = value

    @property
    def audit_feedback(self) -> list[str]:
        return self.audit.audit_feedback

    @audit_feedback.setter
    def audit_feedback(self, value: list[str]) -> None:
        self.audit.audit_feedback = value

    @property
    def audit_result(self) -> AuditResult | None:
        return self.audit.audit_result

    @audit_result.setter
    def audit_result(self, value: AuditResult | None) -> None:
        self.audit.audit_result = value

    @property
    def last_audited_commit(self) -> str | None:
        return self.audit.last_audited_commit

    @last_audited_commit.setter
    def last_audited_commit(self, value: str | None) -> None:
        self.audit.last_audited_commit = value

    @property
    def uat_execution_state(self) -> UatExecutionState | None:
        return self.uat.uat_execution_state

    @uat_execution_state.setter
    def uat_execution_state(self, value: UatExecutionState | None) -> None:
        self.uat.uat_execution_state = value

    @property
    def current_fix_plan(self) -> FixPlanSchema | None:
        return self.uat.current_fix_plan

    @current_fix_plan.setter
    def current_fix_plan(self, value: FixPlanSchema | None) -> None:
        self.uat.current_fix_plan = value

    @property
    def uat_retry_count(self) -> int:
        return self.uat.uat_retry_count

    @uat_retry_count.setter
    def uat_retry_count(self, value: int) -> None:
        self.uat.uat_retry_count = value

    @property
    def requested_cycle_count(self) -> int | None:
        return self.config.requested_cycle_count

    @requested_cycle_count.setter
    def requested_cycle_count(self, value: int | None) -> None:
        self.config.requested_cycle_count = value

    @property
    def self_critic_completed(self) -> bool:
        return self.session.self_critic_completed

    @self_critic_completed.setter
    def self_critic_completed(self, value: bool) -> None:
        self.session.self_critic_completed = value

    @property
    def planned_cycle_count(self) -> int | None:
        return self.config.planned_cycle_count

    @planned_cycle_count.setter
    def planned_cycle_count(self, value: int | None) -> None:
        self.config.planned_cycle_count = value

    @property
    def tdd_phase(self) -> Literal["red", "green"] | None:
        return self.test.tdd_phase

    @tdd_phase.setter
    def tdd_phase(self, value: Literal["red", "green"] | None) -> None:
        self.test.tdd_phase = value

    @property
    def structural_report(self) -> StructuralGateReport | None:
        return self.test.structural_report

    @structural_report.setter
    def structural_report(self, value: StructuralGateReport | None) -> None:
        self.test.structural_report = value

    @property
    def test_logs(self) -> str:
        return self.test.test_logs

    @test_logs.setter
    def test_logs(self, value: str) -> None:
        self.test.test_logs = value

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    # LangGraph internally injects these
    langgraph_step: int | None = None
    langgraph_node: str | None = None
    langgraph_triggers: list[Any] | None = None
    langgraph_path: tuple[Any, ...] | None = None
    langgraph_checkpoint: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _map_legacy_kwargs(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        legacy_mappings = {
            "committee": [
                "current_auditor_index",
                "current_auditor_review_count",
                "iteration_count",
                "is_refactoring",
                "audit_attempt_count",
                "fallback_count",
                "anti_patterns_memory",
            ],
            "session": [
                "jules_session_name",
                "critic_retry_count",
                "pr_url",
                "resume_mode",
                "active_branch",
                "project_session_id",
                "feature_branch",
                "integration_branch",
                "is_session_finalized",
            ],
            "audit": [
                "audit_result",
                "audit_feedback",
                "audit_pass_count",
                "audit_retries",
                "audit_logs",
                "last_audited_commit",
            ],
            "test": ["structural_report", "test_logs", "test_exit_code", "tdd_phase"],
            "uat": [
                "uat_analysis",
                "uat_execution_state",
                "current_fix_plan",
                "sandbox_artifacts",
                "uat_retry_count",
            ],
            "config": ["planned_cycle_count", "requested_cycle_count", "planned_cycles"],
        }

        for sub_model, keys in legacy_mappings.items():
            extracted = {k: data.pop(k) for k in keys if k in data}
            if extracted:
                if sub_model in data and isinstance(data[sub_model], dict):
                    data[sub_model].update(extracted)
                elif sub_model in data and hasattr(data[sub_model], "__dict__"):
                    for k, v in extracted.items():
                        setattr(data[sub_model], k, v)
                else:
                    data[sub_model] = extracted

        return data

    @model_validator(mode="after")
    def do_validate_state_consistency(self) -> "CycleState":
        from src.state_validators import validate_state_consistency

        return validate_state_consistency(self)  # type: ignore[no-any-return]

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class IntegrationState(BaseModel):
    """LangGraph state for Master Integrator concurrent execution."""

    branches_to_merge: list[str] = Field(default_factory=list)
    master_integrator_session_id: str | None = None
    unresolved_conflicts: list[ConflictRegistryItem] = Field(default_factory=list)
    conflict_status: str | None = None
    status: str | None = None

    langgraph_step: int | None = None
    langgraph_node: str | None = None
    langgraph_triggers: list[Any] | None = None
    langgraph_path: tuple[Any, ...] | None = None
    langgraph_checkpoint: dict[str, Any] | None = None

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    model_config = ConfigDict(extra="forbid", validate_assignment=True)
