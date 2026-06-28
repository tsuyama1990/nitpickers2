"""Unit tests for CycleNodes — interface conformance and basic wiring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.graph_nodes import CycleNodes
from src.services.git_ops import GitManager
from src.state import CycleState, IntegrationState

# ---------------------------------------------------------------------------
#  Interface conformance
# ---------------------------------------------------------------------------


class TestCycleNodesInterface:
    """Verify CycleNodes has all required methods."""

    def test_has_all_required_routers(self) -> None:
        """All router methods defined in Any must exist."""
        jules = MagicMock()
        nodes = CycleNodes(jules_client=jules)

        router_methods = [
            "check_coder_outcome",
            "route_architect_critic",
            "route_architect_session",
            "route_committee",
            "route_qa",
            "route_merge",
        ]
        for name in router_methods:
            assert hasattr(nodes, name), f"CycleNodes missing router method '{name}'"
            assert callable(getattr(nodes, name)), f"'{name}' is not callable"

    def test_has_all_required_async_nodes(self) -> None:
        """All async node methods defined in Any must exist."""
        jules = MagicMock()
        nodes = CycleNodes(jules_client=jules)

        async_methods = [
            "architect_session_node",
            "architect_critic_node",
            "test_coder_node",
            "impl_coder_node",
            "auditor_node",
            "committee_manager_node",
            "self_critic_node",
            "final_critic_node",
            "refactor_node",
            "global_refactor_node",
            "qa_session_node",
            "qa_auditor_node",
            "uat_evaluate_node",
            "ux_auditor_node",
            "git_merge_node",
            "master_integrator_node",
            "integration_fixer_node",
        ]
        for name in async_methods:
            assert hasattr(nodes, name), f"CycleNodes missing async method '{name}'"
            assert callable(getattr(nodes, name)), f"'{name}' is not callable"

    def test_has_llm_reviewer_attribute(self) -> None:
        """Any requires llm_reviewer attribute."""
        jules = MagicMock()
        nodes = CycleNodes(jules_client=jules)
        assert hasattr(nodes, "llm_reviewer"), "CycleNodes missing 'llm_reviewer' attribute"


# ---------------------------------------------------------------------------
#  Router delegation
# ---------------------------------------------------------------------------


class TestCycleNodesRouterDelegation:
    """Verify CycleNodes routers correctly delegate to standalone router functions."""

    def test_check_coder_outcome_delegation(self) -> None:
        jules = MagicMock()
        nodes = CycleNodes(jules_client=jules)
        state = CycleState(cycle_id="01", status=None)
        result = nodes.check_coder_outcome(state)
        assert isinstance(result, str)

    def test_route_committee_delegation(self) -> None:
        jules = MagicMock()
        nodes = CycleNodes(jules_client=jules)
        state = CycleState(cycle_id="01")
        result = nodes.route_committee(state)
        assert isinstance(result, str)

    def test_route_architect_critic_delegation(self) -> None:
        jules = MagicMock()
        nodes = CycleNodes(jules_client=jules)
        state = CycleState(cycle_id="01")
        result = nodes.route_architect_critic(state)
        assert isinstance(result, str)

    def test_route_architect_session_delegation(self) -> None:
        jules = MagicMock()
        nodes = CycleNodes(jules_client=jules)
        state = CycleState(cycle_id="01")
        result = nodes.route_architect_session(state)
        assert isinstance(result, str)

    def test_route_qa_delegation(self) -> None:
        jules = MagicMock()
        nodes = CycleNodes(jules_client=jules)
        state = CycleState(cycle_id="01")
        result = nodes.route_qa(state)
        assert isinstance(result, str)

    def test_route_merge_delegation(self) -> None:
        jules = MagicMock()
        nodes = CycleNodes(jules_client=jules)
        state = IntegrationState()
        result = nodes.route_merge(state)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
#  Dependency injection
# ---------------------------------------------------------------------------


class TestCycleNodesDI:
    """Verify dependency injection works correctly."""

    def test_default_instantiation(self) -> None:
        """CycleNodes should be instantiable with just jules_client."""
        jules = MagicMock()
        nodes = CycleNodes(jules_client=jules)
        assert nodes.jules is jules
        assert nodes.git is not None
        assert nodes.llm_reviewer is not None

    def test_custom_dependencies_injected(self) -> None:
        """All optional dependencies should be injectable."""
        jules = MagicMock()
        git = MagicMock(spec=GitManager)
        llm = MagicMock()
        nodes = CycleNodes(jules_client=jules, git_manager=git, llm_reviewer=llm)
        assert nodes.jules is jules
        assert nodes.git is git
        assert nodes.llm_reviewer is llm

    @patch("src.graph_nodes.GitManager")
    @patch("src.graph_nodes.LLMReviewer")
    def test_sub_nodes_initialized(
        self,
        mock_llm: MagicMock,
        mock_git: MagicMock,
    ) -> None:
        """Verify sub-nodes (ArchitectNodes, CoderNodes, etc) are initialized."""
        jules = MagicMock()
        git = MagicMock(spec=GitManager)
        llm = MagicMock()
        nodes = CycleNodes(jules_client=jules, git_manager=git, llm_reviewer=llm)

        # Access private attributes to verify they're initialized
        assert nodes._architect is not None
        assert nodes._architect_critic is not None
        assert nodes._coder is not None
        assert nodes._coder_critic is not None
        assert nodes._auditor is not None
        assert nodes._committee_usecase is not None
        assert nodes._uat_usecase is not None
        assert nodes._ux_auditor_usecase is not None
        assert nodes._qa_usecase is not None
        assert nodes._refactor_usecase is not None
        assert nodes._global_refactor is not None

    @pytest.mark.asyncio
    async def test_architect_node_awaitable(self) -> None:
        """Verify async node methods can be awaited (not checked for correctness)."""
        jules = MagicMock()
        git = MagicMock(spec=GitManager)
        nodes = CycleNodes(jules_client=jules, git_manager=git)

        # Patch sub-nodes to return predictable values
        nodes._architect = MagicMock()
        nodes._architect.architect_session_node = AsyncMock(return_value={"cycle_id": "01"})
        nodes._architect_critic = MagicMock()
        nodes._architect_critic.architect_critic_node = AsyncMock(return_value={})

        state = CycleState(cycle_id="01")
        result = await nodes.architect_session_node(state)
        assert result == {"cycle_id": "01"}
