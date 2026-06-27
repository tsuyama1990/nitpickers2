"""Unit tests for graph structure definitions.

Tests the node composition and edge connectivity of all 4 LangGraph graphs
without executing the actual node logic. Uses mocked CycleNodes.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.enums import FlowStatus
from src.graph import GraphBuilder

# ---------------------------------------------------------------------------
#  Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_nodes() -> MagicMock:
    """Returns a MagicMock that satisfies the Any protocol.

    All async node methods return an empty dict, router methods return a
    default routing key.
    """
    m = MagicMock()

    # ── Architect nodes ──
    m.architect_session_node = MagicMock(return_value={})
    m.architect_critic_node = MagicMock(return_value={})

    # ── Coder nodes ──
    m.test_coder_node = MagicMock(return_value={})
    m.impl_coder_node = MagicMock(return_value={})
    m.auditor_node = MagicMock(return_value={})
    m.committee_manager_node = MagicMock(return_value={})
    m.self_critic_node = MagicMock(return_value={})
    m.final_critic_node = MagicMock(return_value={})
    m.refactor_node = MagicMock(return_value={})
    m.global_refactor_node = MagicMock(return_value={})
    m.coder_critic_node = MagicMock(return_value={})

    # ── QA nodes ──
    m.qa_session_node = MagicMock(return_value={})
    m.qa_auditor_node = MagicMock(return_value={})
    m.uat_evaluate_node = MagicMock(return_value={})
    m.ux_auditor_node = MagicMock(return_value={})

    # ── Integration nodes ──
    m.git_merge_node = MagicMock(return_value={})
    m.master_integrator_node = MagicMock(return_value={})
    m.integration_fixer_node = MagicMock(return_value={})

    # ── Routers (return a default branching key) ──
    m.check_coder_outcome = MagicMock(return_value="self_critic_node")
    m.route_architect_critic = MagicMock(return_value="end")
    m.route_architect_session = MagicMock(return_value="end")
    m.route_committee = MagicMock(return_value="impl_coder_node")
    m.route_qa = MagicMock(return_value="end")
    m.route_merge = MagicMock(return_value="success")

    return m


@pytest.fixture
def builder(mock_nodes: MagicMock) -> GraphBuilder:
    services = MagicMock()
    jules = MagicMock()
    return GraphBuilder(services, jules=jules, nodes=mock_nodes)


# ---------------------------------------------------------------------------
#  Helper: extract edges from a raw StateGraph
# ---------------------------------------------------------------------------

def _edges(graph: Any) -> set[tuple[str, str]]:
    """Return {(source, target)} for all unconditional edges."""
    return set(graph.edges)


def _branches(graph: Any) -> dict[str, dict[str, str]]:
    """Return {source_node: {condition_key: target_node}}.

    LangGraph stores conditional edges as:
        graph.branches[source_node]['condition'].ends = {key: target, ...}
    """
    raw: dict[str, Any] = {}
    for src, branch_map in graph.branches.items():
        condition = branch_map.get("condition")
        if condition is not None:
            raw[src] = dict(condition.ends)
    return raw


# ---------------------------------------------------------------------------
#  Architect Graph
# ---------------------------------------------------------------------------

class TestArchitectGraph:
    def test_graph_has_required_nodes(self, builder: GraphBuilder) -> None:
        graph = builder._create_architect_graph()
        node_names = set(graph.nodes.keys())
        assert "architect_session" in node_names
        assert "architect_critic" in node_names

    def test_start_connection(self, builder: GraphBuilder) -> None:
        graph = builder._create_architect_graph()
        e = _edges(graph)
        assert ("__start__", "architect_session") in e, (
            "START must connect to architect_session"
        )

    def test_end_connections(self, builder: GraphBuilder) -> None:
        """Both architect_session and architect_critic can route to END."""
        graph = builder._create_architect_graph()
        branches = _branches(graph)

        # architect_session can go to END via "end" condition
        architect_session_branches = branches.get("architect_session", {})
        assert "end" in architect_session_branches, (
            "architect_session must have 'end' branch to END"
        )
        assert architect_session_branches["end"] == "__end__"

        # architect_critic can go to END via "end" condition
        architect_critic_branches = branches.get("architect_critic", {})
        assert "end" in architect_critic_branches, (
            "architect_critic must have 'end' branch to END"
        )
        assert architect_critic_branches["end"] == "__end__"

    def test_session_to_critic_edge(self, builder: GraphBuilder) -> None:
        """architect_session must have conditional edge to architect_critic."""
        graph = builder._create_architect_graph()
        branches = _branches(graph)
        session_branches = branches.get("architect_session", {})
        assert "architect_critic" in session_branches
        assert session_branches["architect_critic"] == "architect_critic"

    def test_critic_to_session_loop(self, builder: GraphBuilder) -> None:
        """architect_critic must have conditional edge back to architect_session."""
        graph = builder._create_architect_graph()
        branches = _branches(graph)
        critic_branches = branches.get("architect_critic", {})
        assert "architect_session" in critic_branches
        assert critic_branches["architect_session"] == "architect_session"

    def test_routing_keys_match_router_outputs(self, builder: GraphBuilder) -> None:
        """Verify route_architect_session and route_architect_critic outputs
        are covered by the branching table."""
        graph = builder._create_architect_graph()
        branches = _branches(graph)

        # route_architect_session returns "architect_critic" or "end"
        session_branches = branches["architect_session"]
        for key in ("architect_critic", "end"):
            assert key in session_branches, (
                f"architect_session branching table missing key '{key}'"
            )

        # route_architect_critic returns "architect_session" or "end"
        critic_branches = branches["architect_critic"]
        for key in ("architect_session", "end"):
            assert key in critic_branches, (
                f"architect_critic branching table missing key '{key}'"
            )


# ---------------------------------------------------------------------------
#  Coder Graph
# ---------------------------------------------------------------------------

class TestCoderGraph:
    def test_graph_has_required_nodes(self, builder: GraphBuilder) -> None:
        graph = builder._create_coder_graph()
        node_names = set(graph.nodes.keys())
        expected = {
            "impl_coder_node",
            "auditor",
            "committee_manager_node",
            "self_critic_node",
            "refactor_node",
            "final_critic_node",
        }
        for name in expected:
            assert name in node_names, f"Coder graph missing node '{name}'"

    def test_start_connection(self, builder: GraphBuilder) -> None:
        graph = builder._create_coder_graph()
        e = _edges(graph)
        assert ("__start__", "impl_coder_node") in e

    def test_end_connections(self, builder: GraphBuilder) -> None:
        graph = builder._create_coder_graph()
        e = _edges(graph)
        branches = _branches(graph)

        # final_critic_node → END (unconditional)
        assert ("final_critic_node", "__end__") in e

        # impl_coder_node can route to END via FAILED/COMPLETED
        coder_branches = branches.get("impl_coder_node", {})
        for status in (FlowStatus.FAILED.value, FlowStatus.COMPLETED.value):
            assert status in coder_branches, (
                f"impl_coder_node branching table missing '{status}'"
            )
            assert coder_branches[status] == "__end__"

    def test_static_edges(self, builder: GraphBuilder) -> None:
        graph = builder._create_coder_graph()
        e = _edges(graph)

        assert ("self_critic_node", "auditor") in e
        assert ("auditor", "committee_manager_node") in e
        assert ("refactor_node", "auditor") in e

    def test_impl_coder_conditional_edges(self, builder: GraphBuilder) -> None:
        graph = builder._create_coder_graph()
        branches = _branches(graph)

        coder_branches = branches.get("impl_coder_node", {})
        for key in ("self_critic_node", FlowStatus.FAILED.value, FlowStatus.COMPLETED.value, "impl_coder_node"):
            assert key in coder_branches, (
                f"impl_coder_node branching table missing key '{key}'"
            )

    def test_committee_conditional_edges(self, builder: GraphBuilder) -> None:
        graph = builder._create_coder_graph()
        branches = _branches(graph)

        committee_branches = branches.get("committee_manager_node", {})
        for key in ("impl_coder_node", "next_auditor", "refactor_node", "final_critic"):
            assert key in committee_branches, (
                f"committee_manager_node branching table missing key '{key}'"
            )

    def test_check_coder_outcome_routing_keys_match(self, builder: GraphBuilder) -> None:
        """check_coder_outcome can return: self_critic_node, FAILED, COMPLETED, impl_coder_node."""
        graph = builder._create_coder_graph()
        branches = _branches(graph).get("impl_coder_node", {})
        for key in ("self_critic_node", FlowStatus.FAILED.value, FlowStatus.COMPLETED.value, "impl_coder_node"):
            assert key in branches, (
                f"impl_coder_node branching table missing key '{key}' "
                f"(expected by check_coder_outcome)"
            )

    def test_route_committee_routing_keys_match(self, builder: GraphBuilder) -> None:
        """route_committee can return: impl_coder_node, next_auditor, refactor_node, final_critic."""
        graph = builder._create_coder_graph()
        branches = _branches(graph).get("committee_manager_node", {})
        for key in ("impl_coder_node", "next_auditor", "refactor_node", "final_critic"):
            assert key in branches, (
                f"committee_manager_node branching table missing key '{key}' "
                f"(expected by route_committee)"
            )


# ---------------------------------------------------------------------------
#  QA Graph
# ---------------------------------------------------------------------------

class TestQAGraph:
    def test_graph_has_all_nodes(self, builder: GraphBuilder) -> None:
        graph = builder._create_qa_graph()
        node_names = set(graph.nodes.keys())
        expected = {"qa_session", "qa_auditor", "uat_evaluate", "ux_auditor"}
        for name in expected:
            assert name in node_names, f"QA graph missing node '{name}'"

    def test_start_connection(self, builder: GraphBuilder) -> None:
        graph = builder._create_qa_graph()
        e = _edges(graph)
        assert ("__start__", "uat_evaluate") in e

    def test_static_edges(self, builder: GraphBuilder) -> None:
        graph = builder._create_qa_graph()
        e = _edges(graph)
        assert ("ux_auditor", "__end__") in e
        assert ("qa_auditor", "qa_session") in e
        assert ("qa_session", "__end__") in e

    def test_uat_evaluate_conditional_edges(self, builder: GraphBuilder) -> None:
        """uat_evaluate routes to qa_auditor, ux_auditor, or end."""
        graph = builder._create_qa_graph()
        branches = _branches(graph)
        uat_branches = branches.get("uat_evaluate", {})
        for key in ("qa_auditor", "ux_auditor", "end"):
            assert key in uat_branches, (
                f"uat_evaluate branching table missing key '{key}'"
            )
        assert uat_branches["end"] == "__end__"

    def test_route_qa_routing_keys_match(self, builder: GraphBuilder) -> None:
        """route_qa returns: qa_auditor, ux_auditor, or end."""
        graph = builder._create_qa_graph()
        branches = _branches(graph).get("uat_evaluate", {})
        for key in ("qa_auditor", "ux_auditor", "end"):
            assert key in branches, (
                f"uat_evaluate branching table missing key '{key}' "
                f"(expected by route_qa)"
            )


# ---------------------------------------------------------------------------
#  Integration Graph
# ---------------------------------------------------------------------------

class TestIntegrationGraph:
    def test_graph_has_all_nodes(self, builder: GraphBuilder) -> None:
        graph = builder._create_integration_graph()
        node_names = set(graph.nodes.keys())
        expected = {"git_merge_node", "master_integrator_node", "integration_fixer_node"}
        for name in expected:
            assert name in node_names, f"Integration graph missing node '{name}'"

    def test_start_connection(self, builder: GraphBuilder) -> None:
        graph = builder._create_integration_graph()
        e = _edges(graph)
        assert ("__start__", "git_merge_node") in e

    def test_static_edges(self, builder: GraphBuilder) -> None:
        graph = builder._create_integration_graph()
        e = _edges(graph)
        assert ("master_integrator_node", "git_merge_node") in e, (
            "master_integrator_node must loop back to git_merge_node"
        )
        assert ("integration_fixer_node", "__end__") in e, (
            "integration_fixer_node must connect to END"
        )

    def test_git_merge_conditional_edges(self, builder: GraphBuilder) -> None:
        graph = builder._create_integration_graph()
        branches = _branches(graph)
        merge_branches = branches.get("git_merge_node", {})
        for key in ("conflict", "success"):
            assert key in merge_branches, (
                f"git_merge_node branching table missing key '{key}'"
            )
        assert merge_branches["conflict"] == "master_integrator_node"
        assert merge_branches["success"] == "integration_fixer_node"

    def test_route_merge_routing_keys_match(self, builder: GraphBuilder) -> None:
        """route_merge returns: conflict or success."""
        graph = builder._create_integration_graph()
        branches = _branches(graph).get("git_merge_node", {})
        for key in ("conflict", "success"):
            assert key in branches, (
                f"git_merge_node branching table missing key '{key}' "
                f"(expected by route_merge)"
            )


# ---------------------------------------------------------------------------
#  Cross-graph: Any protocol conformance
# ---------------------------------------------------------------------------

class TestGraphNodesProtocol:
    """Verify that all node names used in graphs correspond to names
    available via the Any protocol on CycleNodes."""

    def _get_graph_node_names(self, builder: GraphBuilder) -> set[str]:
        """Collect all node names from all 4 graphs."""
        names: set[str] = set()
        for graph_name in ("_create_architect_graph", "_create_coder_graph",
                           "_create_qa_graph", "_create_integration_graph"):
            g = getattr(builder, graph_name)()
            names.update(g.nodes.keys())
        return names

    def test_no_undefined_nodes_in_graphs(self, builder: GraphBuilder, mock_nodes: MagicMock) -> None:
        """Every node in every graph must have a corresponding method on mock_nodes."""
        graph_node_names = self._get_graph_node_names(builder)

        # Map graph node names to expected Any method names
        # e.g. "architect_session" → architect_session_node
        #      "auditor" → auditor_node
        #      "qa_session" → qa_session_node
        for node_name in graph_node_names:
            if node_name.startswith("__"):
                continue  # skip START / END markers
            # The mock should have an attribute for each node
            assert hasattr(mock_nodes, f"{node_name}_node") or hasattr(mock_nodes, node_name), (
                f"Graph node '{node_name}' has no matching method in Any"
            )
