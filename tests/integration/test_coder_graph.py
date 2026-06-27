from unittest.mock import MagicMock

from src.graph import GraphBuilder


def test_coder_graph_structure() -> None:
    from src.services.jules_client import JulesClient

    jules_mock = MagicMock(spec=JulesClient)
    services_mock = MagicMock()
    from unittest.mock import patch

    with (
        patch("src.graph_nodes.ArchitectNodes"),
        patch("src.graph_nodes.GitManager"),
        patch("src.graph_nodes.LLMReviewer"),
        patch("src.graph.CycleNodes"),
    ):
        builder = GraphBuilder(services_mock, jules=jules_mock)

    # Mock node attributes
    builder.nodes = MagicMock()
    builder.nodes.test_coder_node = MagicMock()
    builder.nodes.impl_coder_node = MagicMock()
    builder.nodes.auditor_node = MagicMock()
    builder.nodes.self_critic_node = MagicMock()
    builder.nodes.refactor_node = MagicMock()
    builder.nodes.final_critic_node = MagicMock()
    builder.nodes.committee_manager_node = MagicMock()

    builder.nodes.check_coder_outcome = MagicMock()
    builder.nodes.route_committee = MagicMock()

    graph = builder._create_coder_graph()

    edges = set()
    for edge in graph.edges:
        edges.add((edge[0], edge[1]))

    # Assert basic graph edges
    assert ("self_critic_node", "auditor") in edges
    assert ("auditor", "committee_manager_node") in edges
    assert ("refactor_node", "auditor") in edges
    assert ("final_critic_node", "__end__") in edges

    # Assert committee_manager_node has conditional routing
    branches = graph.branches
    committee_branches = branches.get("committee_manager_node")
    assert committee_branches is not None
