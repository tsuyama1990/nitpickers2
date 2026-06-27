"""Consolidated domain models for the nitpickers project."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .multimodal_artifact_schema import MultiModalArtifact

# ---------------------------------------------------------------------------
#  Architecture
# ---------------------------------------------------------------------------

class SystemArchitecture(BaseModel):
    """High-level System Architecture Design."""

    model_config = ConfigDict(extra="forbid")

    project_name: str = Field(..., description="Name of the project")
    background: str = Field(..., description="Project background and context")
    core_philosophy: str = Field(
        ..., description="Core design philosophy (e.g., minimalist, robust, rapid)"
    )
    user_stories: list[str] = Field(..., description="High-level user stories")
    system_design: str = Field(..., description="Overall system design and architecture pattern")
    module_structure: str = Field(
        ..., description="Breakdown of key modules and responsibilities"
    )
    tech_stack: list[str] = Field(..., description="List of technologies and libraries")
    implementation_roadmap: list[str] = Field(..., description="Step-by-step implementation phases")


# ---------------------------------------------------------------------------
#  Critic
# ---------------------------------------------------------------------------

class CriticResult(BaseModel):
    """Result of the Architect Self-Critic evaluation."""

    model_config = ConfigDict(extra="forbid")

    is_approved: bool = Field(description="Whether the architecture is approved.")
    vulnerabilities: list[str] = Field(
        default_factory=list, description="Identified vulnerabilities."
    )
    suggestions: list[str] = Field(default_factory=list, description="Suggestions for improvement.")


# ---------------------------------------------------------------------------
#  Execution
# ---------------------------------------------------------------------------

class UatAnalysis(BaseModel):
    """UAT execution analysis."""

    model_config = ConfigDict(extra="forbid")
    verdict: Literal["PASS", "FAIL"]
    summary: str
    behavior_analysis: str


class ConflictResolutionSchema(BaseModel):
    """Schema for the structured output of the Master Integrator."""

    model_config = ConfigDict(extra="forbid")
    resolved_code: str = Field(..., description="The fully resolved file content.")


class ConflictRegistryItem(BaseModel):
    """Tracks unresolved merge conflicts for an AI cycle."""

    model_config = ConfigDict(extra="forbid")
    file_path: str = Field(..., description="Path to the file with conflicts")
    conflict_markers: list[str] = Field(..., description="List of markers detected")
    resolution_attempts: int = Field(default=0, description="Number of attempts to resolve")
    resolved: bool = Field(default=False, description="Whether the conflict is resolved")


# ---------------------------------------------------------------------------
#  File Operations
# ---------------------------------------------------------------------------

class FileArtifact(BaseModel):
    """Generated or modified file artifact."""

    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., description="File path (e.g. dev_documents/CYCLE01/SPEC.md)")
    content: str = Field(..., description="File content")
    language: str = Field("markdown", description="Language (python, markdown, etc.)")


class FileCreate(BaseModel):
    """New file creation."""

    model_config = ConfigDict(extra="forbid")
    operation: Literal["create"] = "create"
    path: str = Field(..., description="Path to the file to create")
    content: str = Field(..., description="Full content of the new file")


class FilePatch(BaseModel):
    """Existing file modification via patch."""

    model_config = ConfigDict(extra="forbid")
    operation: Literal["patch"] = "patch"
    path: str = Field(..., description="Path to the file to modify")
    search_block: str = Field(..., description="Exact block to search for")
    replace_block: str = Field(..., description="New block to replace with")


FileOperation = FileCreate | FilePatch


# ---------------------------------------------------------------------------
#  Fix Plan Schema
# ---------------------------------------------------------------------------

class FilePatchEntry(BaseModel):
    """Single file patch entry within a fix plan."""

    target_file: str = Field(..., description="The exact path of the file to modify.")
    git_diff_patch: str = Field(..., description="The code snippet or diff for this file.")


class FixPlanSchema(BaseModel):
    """Structured JSON Fix Plan for bug remediation."""

    model_config = ConfigDict(extra="forbid")
    defect_description: str = Field(..., description="Clear reasoning of the defect.")
    patches: list[FilePatchEntry] = Field(
        ..., description="List of files and their modifications."
    )


# ---------------------------------------------------------------------------
#  Manifest
# ---------------------------------------------------------------------------

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
    jules_session_id: str | None = Field(default=None, description="Active AI session ID")
    current_iteration: int = 1
    pr_url: str | None = None
    last_error: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    session_restart_count: int = Field(default=0)
    max_session_restarts: int = Field(default=4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProjectManifest(BaseModel):
    """Root manifest for the entire project state."""

    model_config = ConfigDict(extra="forbid")
    project_session_id: str
    feature_branch: str | None = None
    integration_branch: str
    qa_session_id: str | None = Field(default=None)
    cycles: list[CycleManifest] = Field(default_factory=list)
    unresolved_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    last_updated: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
#  Refactor
# ---------------------------------------------------------------------------

class GlobalRefactorResult(BaseModel):
    """Result of the global refactoring analysis and execution."""

    model_config = ConfigDict(extra="forbid")
    refactorings_applied: bool = Field(default=False)
    modified_files: list[str] = Field(default_factory=list)
    summary: str = Field(default="", description="Summary of refactorings applied.")


# ---------------------------------------------------------------------------
#  Review
# ---------------------------------------------------------------------------

class ReviewIssue(BaseModel):
    """個別の指摘事項."""

    model_config = ConfigDict(extra="forbid")
    category: Literal[
        "Hardcoding", "Scalability", "Security", "Architecture",
        "Type Safety", "Logic Error", "Test Coverage", "Other",
    ] = Field(description="Issue category.")
    severity: Literal["fatal", "warning"] = Field(description="Severity of the issue.")
    file_path: str = Field(description="Exact file path.")
    target_code_snippet: str = Field(description="Specific snippet (1-3 lines).")
    issue_description: str = Field(description="Description of the issue.")
    concrete_fix: str = Field(description="Exact code or structural change required.")


class AuditorReport(BaseModel):
    """レポート全体."""

    model_config = ConfigDict(extra="forbid")
    is_passed: bool = Field(description="False if there is at least one issue.")
    summary: str = Field(description="Brief summary of the review.")
    issues: list[ReviewIssue] = Field(default_factory=list)


class AuditResult(BaseModel):
    """Audit result."""

    model_config = ConfigDict(extra="forbid")
    status: str | None = None
    is_approved: bool = False
    reason: str | None = None
    feedback: str | None = None
    critical_issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class PlanAuditResult(BaseModel):
    """Result of AI-on-AI Plan Audit."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["APPROVED", "REJECTED"]
    reason: str
    feedback: str | None = Field(default="", description="Mandatory if REJECTED")


# ---------------------------------------------------------------------------
#  Spec
# ---------------------------------------------------------------------------

class CyclePlan(BaseModel):
    """Planning phase artifacts."""

    model_config = ConfigDict(extra="forbid")
    spec_file: FileArtifact
    schema_file: FileArtifact
    uat_file: FileArtifact
    thought_process: str = Field(..., description="Thought process behind the design")


class Feature(BaseModel):
    name: str
    description: str
    priority: Literal["High", "Medium", "Low"]
    acceptance_criteria: list[str]


class TechnicalConstraint(BaseModel):
    category: str
    description: str


class StructuredSpec(BaseModel):
    """Structured representation of ALL_SPEC.md."""

    project_name: str
    version: str = "1.0.0"
    overview: str = Field(..., description="Executive summary")
    goals: list[str] = Field(..., description="Primary goals")
    architecture_overview: str = Field(..., description="High-level design")
    features: list[Feature] = Field(..., description="Initial backlog")
    constraints: list[TechnicalConstraint] = Field(default_factory=list)
    terminology: dict[str, str] = Field(default_factory=dict, description="Domain glossary")


# ---------------------------------------------------------------------------
#  UAT Execution State
# ---------------------------------------------------------------------------

class UatExecutionState(BaseModel):
    """Execution state of dynamic UAT pipeline."""

    model_config = ConfigDict(extra="forbid")
    exit_code: int = Field(..., description="Exit code of pytest execution.")
    stdout: str = Field(default="", description="Standard output.")
    stderr: str = Field(default="", description="Standard error.")
    artifacts: list[MultiModalArtifact] = Field(
        default_factory=list, description="Validated multi-modal artifacts."
    )


# ---------------------------------------------------------------------------
#  UX Audit Report
# ---------------------------------------------------------------------------

class UXViolation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principle: str = Field(..., description="The UI/UX principle violated.")
    element: str = Field(..., description="The specific UI element.")
    issue: str = Field(..., description="Description of the UX issue.")
    suggestion: str = Field(..., description="Actionable suggestion.")


class UXAuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overall_score: int = Field(..., description="Overall UX score out of 100.")
    good_points: list[str] = Field(default_factory=list)
    violations: list[UXViolation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
#  Verification Schema
# ---------------------------------------------------------------------------

class VerificationResult(BaseModel):
    """Result of a single mechanical verification step."""

    model_config = ConfigDict(extra="forbid")
    command: str = Field(..., description="The command executed")
    exit_code: int = Field(..., description="Exit code")
    stdout: str = Field(default="", description="Full standard output")
    stderr: str = Field(default="", description="Standard error")
    timeout_occurred: bool = Field(default=False)

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timeout_occurred


class StructuralGateReport(BaseModel):
    """Aggregated report of all mechanical verification checks."""

    model_config = ConfigDict(extra="forbid")
    lint_result: VerificationResult = Field(..., description="Linting result")
    type_check_result: VerificationResult = Field(..., description="Type check result")
    test_result: VerificationResult = Field(..., description="Test result")

    @property
    def passed(self) -> bool:
        return self.lint_result.passed and self.type_check_result.passed and self.test_result.passed

    def get_failure_report(self) -> str:
        report_lines = []
        for name, result in [
            ("Linting", self.lint_result),
            ("Type Checking", self.type_check_result),
            ("Testing", self.test_result),
        ]:
            if not result.passed:
                report_lines.append(f"--- {name} Failed ---")
                report_lines.append(f"Command: {result.command}")
                if result.timeout_occurred:
                    report_lines.append("Reason: TIMEOUT OCCURRED")
                report_lines.append(f"Exit Code: {result.exit_code}")
                err_msg = result.stderr.strip() if result.stderr.strip() else result.stdout.strip()
                report_lines.append(f"Output:\n{err_msg}\n")
        return "\n".join(report_lines)
