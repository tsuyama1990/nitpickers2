"""E2E tests for the Architect Graph — validates actual execution flow."""

from typing import Any
from unittest.mock import MagicMock

from src.enums import FlowStatus
from src.graph import GraphBuilder
from src.state import CycleState


def test_architect_graph_happy_path() -> None:
    """Architect: session → critic → END (completed)."""
    from src.service_container import ServiceContainer
    from src.services.jules_client import JulesClient

    services = MagicMock()
    services.__class__ = ServiceContainer  # type: ignore[assignment]
    jules = MagicMock()
    jules.__class__ = JulesClient  # type: ignore[assignment]

    builder = GraphBuilder(services, jules=jules)
    nodes_mock = MagicMock()

    def mock_session(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.ARCHITECT_SESSION_COMPLETED}

    def mock_critic(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.ARCHITECT_COMPLETED}

    nodes_mock.architect_session_node = mock_session
    nodes_mock.architect_critic_node = mock_critic

    def mock_route_session(state: CycleState) -> str:
        return "architect_critic"

    def mock_route_critic(state: CycleState) -> str:
        return "end"

    nodes_mock.route_architect_session = mock_route_session
    nodes_mock.route_architect_critic = mock_route_critic

    builder.nodes = nodes_mock
    graph = builder.build_architect_graph()

    initial_state = CycleState(cycle_id="01")
    initial_state.status = FlowStatus.START

    for state in graph.stream(initial_state, config={"configurable": {"thread_id": "arch-happy"}}):
        for _node_name, _node_state in state.items():
            pass

    final_state = graph.get_state({"configurable": {"thread_id": "arch-happy"}}).values
    assert final_state["status"] == FlowStatus.ARCHITECT_COMPLETED


def test_architect_graph_critic_loop() -> None:
    """Architect: session → critic (rejected) → session → critic (approved) → END."""
    from src.service_container import ServiceContainer
    from src.services.jules_client import JulesClient

    services = MagicMock()
    services.__class__ = ServiceContainer  # type: ignore[assignment]
    jules = MagicMock()
    jules.__class__ = JulesClient  # type: ignore[assignment]

    builder = GraphBuilder(services, jules=jules)
    nodes_mock = MagicMock()

    call_count = {"critic": 0}

    def mock_session(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.ARCHITECT_SESSION_COMPLETED}

    def mock_critic(state: CycleState) -> dict[str, Any]:
        call_count["critic"] += 1
        if call_count["critic"] == 1:
            return {"status": FlowStatus.ARCHITECT_CRITIC_REJECTED, "session": state.session}
        return {"status": FlowStatus.ARCHITECT_COMPLETED}

    nodes_mock.architect_session_node = mock_session
    nodes_mock.architect_critic_node = mock_critic

    def mock_route_session(state: CycleState) -> str:
        return "architect_critic"

    def mock_route_critic(state: CycleState) -> str:
        if state.status == FlowStatus.ARCHITECT_CRITIC_REJECTED:
            return "architect_session"
        return "end"

    nodes_mock.route_architect_session = mock_route_session
    nodes_mock.route_architect_critic = mock_route_critic

    builder.nodes = nodes_mock
    graph = builder.build_architect_graph()

    initial_state = CycleState(cycle_id="01")

    for state in graph.stream(initial_state, config={"configurable": {"thread_id": "arch-loop"}}):
        for _node_name, _node_state in state.items():
            pass

    final_state = graph.get_state({"configurable": {"thread_id": "arch-loop"}}).values
    assert final_state["status"] == FlowStatus.ARCHITECT_COMPLETED
    assert call_count["critic"] == 2  # Should have looped once


def test_architect_graph_failed() -> None:
    """Architect: session → END with ARCHITECT_FAILED."""
    from src.service_container import ServiceContainer
    from src.services.jules_client import JulesClient

    services = MagicMock()
    services.__class__ = ServiceContainer  # type: ignore[assignment]
    jules = MagicMock()
    jules.__class__ = JulesClient  # type: ignore[assignment]

    builder = GraphBuilder(services, jules=jules)
    nodes_mock = MagicMock()

    def mock_session(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.ARCHITECT_FAILED, "error": "Jules error"}

    def mock_critic(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.ARCHITECT_FAILED}

    nodes_mock.architect_session_node = mock_session
    nodes_mock.architect_critic_node = mock_critic

    def mock_route_session(state: CycleState) -> str:
        return "end"

    def mock_route_critic(state: CycleState) -> str:
        return "end"

    nodes_mock.route_architect_session = mock_route_session
    nodes_mock.route_architect_critic = mock_route_critic

    builder.nodes = nodes_mock
    graph = builder.build_architect_graph()

    initial_state = CycleState(cycle_id="01")

    for state in graph.stream(initial_state, config={"configurable": {"thread_id": "arch-fail"}}):
        for _node_name, _node_state in state.items():
            pass

    final_state = graph.get_state({"configurable": {"thread_id": "arch-fail"}}).values
    assert final_state["status"] == FlowStatus.ARCHITECT_FAILED
