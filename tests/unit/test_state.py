# mypy: disable-error-code="call-arg,arg-type"

"""Unit tests for CycleState and IntegrationState validation.

Tests Pydantic field validators, model validators, and legacy kwarg mapping.
"""


import pytest
from pydantic import ValidationError

from src.config import settings
from src.enums import FlowStatus, WorkPhase
from src.state import (
    CycleState,
    IntegrationState,
    validate_audit_attempt_count,
    validate_auditor_index,
    validate_cycle_id,
    validate_review_count,
    validate_state_consistency,
)

# ---------------------------------------------------------------------------
#  validate_cycle_id
# ---------------------------------------------------------------------------

class TestValidateCycleId:
    def test_valid_cycle_ids(self) -> None:
        for cid in ("01", "qa-tutorials", "CYCLE_3", "a-b_c", "test123"):
            assert validate_cycle_id(cid) == cid

    def test_invalid_cycle_ids(self) -> None:
        for cid in ("", "a b", "cycle/01", "test@id", "with space"):
            with pytest.raises(ValueError, match=r"cycle_id.*invalid"):
                validate_cycle_id(cid)


# ---------------------------------------------------------------------------
#  validate_auditor_index
# ---------------------------------------------------------------------------

class TestValidateAuditorIndex:
    def test_valid_indices(self) -> None:
        for idx in range(1, settings.NUM_AUDITORS + 1):
            assert validate_auditor_index(idx) == idx

    def test_less_than_one(self) -> None:
        with pytest.raises(ValueError, match="must be greater than or equal to 1"):
            validate_auditor_index(0)

    def test_exceeds_max(self) -> None:
        with pytest.raises(ValueError, match=f"exceeds NUM_AUDITORS={settings.NUM_AUDITORS}"):
            validate_auditor_index(settings.NUM_AUDITORS + 1)


# ---------------------------------------------------------------------------
#  validate_review_count
# ---------------------------------------------------------------------------

class TestValidateReviewCount:
    def test_valid_counts(self) -> None:
        for cnt in range(1, settings.REVIEWS_PER_AUDITOR + 1):
            assert validate_review_count(cnt) == cnt

    def test_less_than_one(self) -> None:
        with pytest.raises(ValueError, match="must be greater than or equal to 1"):
            validate_review_count(0)

    def test_exceeds_max(self) -> None:
        with pytest.raises(ValueError, match=f"exceeds REVIEWS_PER_AUDITOR={settings.REVIEWS_PER_AUDITOR}"):
            validate_review_count(settings.REVIEWS_PER_AUDITOR + 1)


# ---------------------------------------------------------------------------
#  validate_audit_attempt_count
# ---------------------------------------------------------------------------

class TestValidateAuditAttemptCount:
    def test_valid_counts(self) -> None:
        # Range: 0 to max_audit_retries + 1
        for cnt in range(settings.max_audit_retries + 2):
            assert validate_audit_attempt_count(cnt) == cnt

    def test_negative(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            validate_audit_attempt_count(-1)

    def test_exceeds_max(self) -> None:
        with pytest.raises(ValueError, match="exceeds absolute maximum threshold"):
            validate_audit_attempt_count(settings.max_audit_retries + 2)


# ---------------------------------------------------------------------------
#  validate_state_consistency
# ---------------------------------------------------------------------------

class TestValidateStateConsistency:
    def test_completed_clears_error(self) -> None:
        """COMPLETED status should clear error field."""
        state = CycleState(cycle_id="01", status=FlowStatus.COMPLETED, error="something went wrong")
        result = validate_state_consistency(state)
        assert result.error is None

    def test_auditor_index_exceeds_max(self) -> None:
        """current_auditor_index > NUM_AUDITORS should raise."""
        state = CycleState(cycle_id="01")
        state.committee.current_auditor_index = settings.NUM_AUDITORS + 1
        with pytest.raises(ValueError, match="logically exceeds maximum"):
            validate_state_consistency(state)


# ---------------------------------------------------------------------------
#  CycleState construction & field validators
# ---------------------------------------------------------------------------

class TestCycleStateConstruction:
    def test_minimal_construction(self) -> None:
        state = CycleState(cycle_id="01")
        assert state.cycle_id == "01"
        assert state.current_phase == WorkPhase.INIT
        assert state.status is None
        assert state.committee is not None
        assert state.session is not None

    def test_invalid_cycle_id_raises(self) -> None:
        with pytest.raises(ValidationError, match=r"cycle_id.*invalid"):
            CycleState(cycle_id="invalid id")

    def test_committee_field_validators(self) -> None:
        """CommitteeState field validators should reject out-of-range values."""
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            CycleState(cycle_id="01", committee={"current_auditor_index": 0})

        with pytest.raises(ValidationError, match="exceeds NUM_AUDITORS"):
            CycleState(cycle_id="01", committee={"current_auditor_index": 999})

        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            CycleState(cycle_id="01", committee={"current_auditor_review_count": 0})

        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            CycleState(cycle_id="01", committee={"audit_attempt_count": -1})

    def test_model_validator_clears_error_on_completed(self) -> None:
        """Setting COMPLETED status should auto-clear error."""
        state = CycleState(cycle_id="01", status=FlowStatus.COMPLETED, error="old error")
        assert state.error is None

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            CycleState(cycle_id="01", unknown_field="should_fail")

    def test_validate_assignment_enforced(self) -> None:
        state = CycleState(cycle_id="01")
        with pytest.raises(ValidationError, match=r"cycle_id.*invalid"):
            state.cycle_id = "bad id"


# ---------------------------------------------------------------------------
#  Legacy kwarg mapping
# ---------------------------------------------------------------------------

class TestSubStateAccess:
    """Verify sub-state construction and property bridge access."""

    def test_committee_sub_state(self) -> None:
        state = CycleState(
            cycle_id="01",
            committee={"current_auditor_index": 2, "is_refactoring": True},
        )
        assert state.committee.current_auditor_index == 2
        assert state.committee.is_refactoring is True
        assert state.committee.anti_patterns_memory == []

    def test_session_sub_state(self) -> None:
        state = CycleState(
            cycle_id="01",
            session={
                "jules_session_name": "sess_abc",
                "pr_url": "http://example.com/pr/1",
                "project_session_id": "proj_123",
                "feature_branch": "feature/y",
            },
        )
        assert state.session.jules_session_name == "sess_abc"
        assert state.session.pr_url == "http://example.com/pr/1"
        assert state.session.project_session_id == "proj_123"
        assert state.session.feature_branch == "feature/y"

    def test_audit_sub_state(self) -> None:
        state = CycleState(
            cycle_id="01",
            audit={"audit_feedback": ["fix this"], "audit_logs": "some logs"},
        )
        assert state.audit.audit_feedback == ["fix this"]
        assert state.audit.audit_logs == "some logs"

    def test_test_sub_state(self) -> None:
        state = CycleState(
            cycle_id="01",
            test={"test_logs": "test output", "tdd_phase": "red"},
        )
        assert state.test.test_logs == "test output"
        assert state.test.tdd_phase == "red"

    def test_uat_sub_state(self) -> None:
        state = CycleState(
            cycle_id="01",
            uat={"uat_retry_count": 2},
        )
        assert state.uat.uat_retry_count == 2

    def test_config_sub_state(self) -> None:
        state = CycleState(
            cycle_id="01",
            config={"planned_cycle_count": 10, "planned_cycles": ["01", "02"]},
        )
        assert state.config.planned_cycle_count == 10
        assert state.config.planned_cycles == ["01", "02"]

    def test_property_bridge_access(self) -> None:
        """Property bridges should still work for sub-state field access."""
        state = CycleState(cycle_id="01")
        state.committee.current_auditor_index = 2
        assert state.current_auditor_index == 2
        assert state.committee.current_auditor_index == 2


# ---------------------------------------------------------------------------
#  IntegrationState
# ---------------------------------------------------------------------------

class TestIntegrationState:
    def test_minimal_construction(self) -> None:
        state = IntegrationState()
        assert state.branches_to_merge == []
        assert state.status is None
        assert state.conflict_status is None

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            IntegrationState(unknown_field="should_fail")

    def test_get_method(self) -> None:
        state = IntegrationState(status="conflict", conflict_status="conflict_detected")
        assert state.get("status") == "conflict"
        assert state.get("conflict_status") == "conflict_detected"
        assert state.get("nonexistent", "default") == "default"
