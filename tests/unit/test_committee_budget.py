"""
Unit tests for committee_manager_node 6-round audit budget enforcement.

These tests verify that:
1. CommitteeUseCase properly enforces the 6-round budget (NUM_AUDITORS=3 x REVIEWS_PER_AUDITOR=2)
2. route_committee correctly maps statuses to graph destinations
3. The budget fallback correctly sets final_fix=True
"""

from unittest.mock import patch

import pytest

from src.domain_models import AuditResult
from src.enums import FlowStatus
from src.nodes.routers import route_committee
from src.state import AuditState, CommitteeState, CycleState


def _make_state(
    auditor_index: int = 1,
    review_count: int = 1,
    iteration_count: int = 0,
    is_approved: bool = False,
    final_fix: bool = False,
    status: FlowStatus = FlowStatus.REJECTED,
) -> CycleState:
    """Helper to build a CycleState for committee testing."""
    audit_result = AuditResult(
        status="APPROVED" if is_approved else "REJECTED",
        is_approved=is_approved,
        reason="test",
        feedback="test feedback",
    )
    committee = CommitteeState(
        current_auditor_index=auditor_index,
        current_auditor_review_count=review_count,
        iteration_count=iteration_count,
    )
    audit = AuditState(audit_result=audit_result)
    return CycleState(
        cycle_id="01",
        status=status,

        committee=committee,
        audit=audit,
    )


class TestCommitteeBudgetEnforcement:
    """Tests for CommitteeUseCase budget enforcement."""

    @pytest.mark.asyncio
    async def test_retry_within_budget(self) -> None:
        """Auditor rejected, within review budget → RETRY_FIX (→ impl_coder_node)."""
        from src.services.committee_usecase import CommitteeUseCase

        # Auditor #1, first review → within budget (max is REVIEWS_PER_AUDITOR=2)
        state = _make_state(auditor_index=1, review_count=1, is_approved=False)

        with patch("src.services.committee_usecase.settings") as mock_settings:
            mock_settings.NUM_AUDITORS = 3
            mock_settings.REVIEWS_PER_AUDITOR = 2

            usecase = CommitteeUseCase()
            result = await usecase.execute(state)

        assert result["status"] == FlowStatus.RETRY_FIX
        assert result.get("current_phase") != WorkPhase.FINAL_CRITIC

    @pytest.mark.asyncio
    async def test_next_auditor_after_review_limit(self) -> None:
        """Auditor #1 exhausted reviews → move to auditor #2."""
        from src.services.committee_usecase import CommitteeUseCase

        # Auditor #1, review_count at max (2), more auditors remain (index < NUM_AUDITORS)
        state = _make_state(auditor_index=1, review_count=2, is_approved=False)

        with patch("src.services.committee_usecase.settings") as mock_settings:
            mock_settings.NUM_AUDITORS = 3
            mock_settings.REVIEWS_PER_AUDITOR = 2

            usecase = CommitteeUseCase()
            result = await usecase.execute(state)

        # Should move to next auditor index
        assert result["status"] == FlowStatus.RETRY_FIX
        committee_state = result["committee"]
        assert committee_state.current_auditor_index == 2, (
            f"Expected auditor index 2, got {committee_state.current_auditor_index}"
        )

    @pytest.mark.asyncio
    async def test_final_fix_when_budget_exhausted(self) -> None:
        """All 6 rounds exhausted → final_fix=True must be returned."""
        from src.services.committee_usecase import CommitteeUseCase

        # Last auditor (index=3), last review (count=2) = round 6 of 6
        state = _make_state(auditor_index=3, review_count=2, is_approved=False)

        with patch("src.services.committee_usecase.settings") as mock_settings:
            mock_settings.NUM_AUDITORS = 3
            mock_settings.REVIEWS_PER_AUDITOR = 2

            usecase = CommitteeUseCase()
            result = await usecase.execute(state)

        assert result.get("current_phase") == WorkPhase.FINAL_CRITIC, (
            f"Budget exhausted: final_fix must be True, got result={result}"
        )

    @pytest.mark.asyncio
    async def test_post_audit_refactor_when_all_approved(self) -> None:
        """All auditors approved → POST_AUDIT_REFACTOR."""
        from src.services.committee_usecase import CommitteeUseCase

        # Last auditor (index=3) approved
        state = _make_state(auditor_index=3, review_count=1, is_approved=True)

        with patch("src.services.committee_usecase.settings") as mock_settings:
            mock_settings.NUM_AUDITORS = 3
            mock_settings.REVIEWS_PER_AUDITOR = 2

            usecase = CommitteeUseCase()
            result = await usecase.execute(state)

        assert result["status"] == FlowStatus.POST_AUDIT_REFACTOR

    @pytest.mark.asyncio
    async def test_exactly_six_rounds_triggers_final_fix(self) -> None:
        """Simulate 6 consecutive rejections and verify final_fix is set at round 6."""
        from src.services.committee_usecase import CommitteeUseCase

        # Simulate all 6 rounds by running through the committee logic
        # Round 1: auditor 1, review 1 → retry
        # Round 2: auditor 1, review 2 → next auditor
        # Round 3: auditor 2, review 1 → retry
        # Round 4: auditor 2, review 2 → next auditor
        # Round 5: auditor 3, review 1 → retry
        # Round 6: auditor 3, review 2 → final_fix=True

        round_configs = [
            (1, 1, False),  # Round 1
            (1, 2, False),  # Round 2 - triggers next auditor
            (2, 1, False),  # Round 3
            (2, 2, False),  # Round 4 - triggers next auditor
            (3, 1, False),  # Round 5
            (3, 2, True),  # Round 6 - triggers final_fix
        ]

        with patch("src.services.committee_usecase.settings") as mock_settings:
            mock_settings.NUM_AUDITORS = 3
            mock_settings.REVIEWS_PER_AUDITOR = 2

            for auditor_idx, review_count, expect_final_fix in round_configs:
                state = _make_state(
                    auditor_index=auditor_idx, review_count=review_count, is_approved=False
                )
                usecase = CommitteeUseCase()
                result = await usecase.execute(state)

                if expect_final_fix:
                    assert result.get("current_phase") == WorkPhase.FINAL_CRITIC, (
                        f"At round {auditor_idx}/{review_count}, expected final_fix=True"
                    )


class TestRouteCommittee:
    """Tests for the route_committee router function."""

    def test_retry_fix_routes_to_impl_coder(self) -> None:
        state = _make_state(status=FlowStatus.RETRY_FIX)
        assert route_committee(state) == "impl_coder_node"

    def test_next_auditor_routes_back_to_auditor(self) -> None:
        state = _make_state(status=FlowStatus.NEXT_AUDITOR)
        assert route_committee(state) == "next_auditor"

    def test_post_audit_refactor_routes_to_refactor_node(self) -> None:
        state = _make_state(status=FlowStatus.POST_AUDIT_REFACTOR)
        assert route_committee(state) == "refactor_node"

    def test_ready_for_audit_routes_to_final_critic(self) -> None:
        state = _make_state(status=FlowStatus.READY_FOR_AUDIT)
        assert route_committee(state) == "final_critic"

    def test_unknown_status_defaults_to_impl_coder(self) -> None:
        state = _make_state(status=FlowStatus.FAILED)
        assert route_committee(state) == "impl_coder_node"
