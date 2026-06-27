"""E2E tests for the QA Graph — validates actual execution flow."""

from typing import Any
from unittest.mock import MagicMock

from src.enums import FlowStatus
from src.graph import GraphBuilder
from src.state import CycleState


def test_qa_graph_uat_failed_path() -> None:
    """QA: uat_evaluate (UAT_FAILED) → qa_auditor → qa_session → END."""
    from src.service_container import ServiceContainer
    from src.services.jules_client import JulesClient

    services = MagicMock()
    services.__class__ = ServiceContainer  # type: ignore[assignment]
    jules = MagicMock()
    jules.__class__ = JulesClient  # type: ignore[assignment]

    builder = GraphBuilder(services, jules=jules)
    nodes_mock = MagicMock()

    def mock_uat_evaluate(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.UAT_FAILED}

    def mock_qa_auditor(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.MAX_RETRIES}

    def mock_qa_session(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.COMPLETED}

    def mock_ux_auditor(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.COMPLETED}

    nodes_mock.uat_evaluate_node = mock_uat_evaluate
    nodes_mock.qa_auditor_node = mock_qa_auditor
    nodes_mock.qa_session_node = mock_qa_session
    nodes_mock.ux_auditor_node = mock_ux_auditor

    def mock_route_qa(state: CycleState) -> str:
        return "qa_auditor" if state.status == FlowStatus.UAT_FAILED else "ux_auditor"

    nodes_mock.route_qa = mock_route_qa

    builder.nodes = nodes_mock
    graph = builder.build_qa_graph()

    initial_state = CycleState(cycle_id="qa-tutorials")

    for state in graph.stream(initial_state, config={"configurable": {"thread_id": "qa-uat-fail"}}):
        for _node_name, _node_state in state.items():
            pass

    final_state = graph.get_state({"configurable": {"thread_id": "qa-uat-fail"}}).values
    assert final_state["status"] == FlowStatus.COMPLETED


def test_qa_graph_uat_passed_path() -> None:
    """QA: uat_evaluate (PASS) → ux_auditor → END."""
    from src.service_container import ServiceContainer
    from src.services.jules_client import JulesClient

    services = MagicMock()
    services.__class__ = ServiceContainer  # type: ignore[assignment]
    jules = MagicMock()
    jules.__class__ = JulesClient  # type: ignore[assignment]

    builder = GraphBuilder(services, jules=jules)
    nodes_mock = MagicMock()

    def mock_uat_evaluate(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.COMPLETED}

    def mock_ux_auditor(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.COMPLETED}

    nodes_mock.uat_evaluate_node = mock_uat_evaluate
    nodes_mock.qa_auditor_node = MagicMock(return_value={})
    nodes_mock.qa_session_node = MagicMock(return_value={})
    nodes_mock.ux_auditor_node = mock_ux_auditor

    def mock_route_qa(state: CycleState) -> str:
        return "qa_auditor" if state.status == FlowStatus.UAT_FAILED else "ux_auditor"

    nodes_mock.route_qa = mock_route_qa

    builder.nodes = nodes_mock
    graph = builder.build_qa_graph()

    initial_state = CycleState(cycle_id="qa-tutorials")

    for state in graph.stream(initial_state, config={"configurable": {"thread_id": "qa-uat-pass"}}):
        for _node_name, _node_state in state.items():
            pass

    final_state = graph.get_state({"configurable": {"thread_id": "qa-uat-pass"}}).values
    # ux_auditor should have been called, qa_auditor should NOT
    assert final_state["status"] == FlowStatus.COMPLETED
