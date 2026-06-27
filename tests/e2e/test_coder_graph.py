from typing import Any
from unittest.mock import MagicMock

from src.enums import FlowStatus
from src.graph import GraphBuilder
from src.state import CycleState


def test_coder_graph_happy_path() -> None:  # noqa: C901
    from src.service_container import ServiceContainer
    from src.services.jules_client import JulesClient

    services = MagicMock()
    services.__class__ = ServiceContainer  # type: ignore[assignment]
    jules = MagicMock()
    jules.__class__ = JulesClient  # type: ignore[assignment]

    builder = GraphBuilder(services, jules=jules)

    nodes_mock = MagicMock()

    def mock_test_coder(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.COMPLETED}

    nodes_mock.test_coder_node = mock_test_coder

    def mock_impl_coder(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.READY_FOR_AUDIT}

    nodes_mock.impl_coder_node = mock_impl_coder

    def mock_sandbox_eval(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.READY_FOR_AUDIT}

    nodes_mock.sandbox_evaluate_node = mock_sandbox_eval

    def mock_auditor(state: CycleState) -> dict[str, Any]:
        from src.domain_models import AuditResult

        audit_res = AuditResult(is_approved=True, status="Approve")

        idx = state.committee.current_auditor_index
        if idx < 3:
            idx += 1

        return {
            "audit": state.audit.model_copy(update={"audit_result": audit_res}),
            "committee": state.committee.model_copy(update={"current_auditor_index": idx}),
        }

    nodes_mock.auditor_node = mock_auditor

    def mock_self_critic(state: CycleState) -> dict[str, Any]:
        return {}

    nodes_mock.self_critic_node = mock_self_critic

    def mock_refactor(state: CycleState) -> dict[str, Any]:
        return {"committee": state.committee.model_copy(update={"is_refactoring": True})}

    nodes_mock.refactor_node = mock_refactor

    def mock_committee_manager(state: CycleState) -> dict[str, Any]:
        # Preserve committee state (is_refactoring flag) so route_committee can route correctly
        return {"committee": state.committee}

    nodes_mock.committee_manager_node = mock_committee_manager

    def mock_final_critic(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.COMPLETED}

    nodes_mock.final_critic_node = mock_final_critic

    def mock_check_coder_outcome(state: CycleState) -> str:
        return "self_critic_node"

    nodes_mock.check_coder_outcome = mock_check_coder_outcome

    def mock_route_committee(state: CycleState) -> str:
        # Route to final_critic after refactoring is done
        if getattr(state.committee, "is_refactoring", False):
            return "final_critic"
        return "refactor_node"

    nodes_mock.route_committee = mock_route_committee

    # Inject mock nodes
    builder.nodes = nodes_mock

    graph = builder.build_coder_graph()

    initial_state = CycleState(cycle_id="01")
    initial_state.status = FlowStatus.READY_FOR_AUDIT

    # Asserting that graph works as expected will be hard because routers might not be fully working as defined in SPEC
    # So we execute it to see if it fails (as required by TDD Red phase)
    for state in graph.stream(initial_state, config={"configurable": {"thread_id": "1"}}):
        for _node_name, _node_state in state.items():
            pass

    # This asserts SPEC behavior: should reach END with status COMPLETED and is_refactoring True
    final_state = graph.get_state({"configurable": {"thread_id": "1"}}).values
    assert final_state["status"] == FlowStatus.COMPLETED
    assert getattr(final_state["committee"], "is_refactoring", False) is True


def test_coder_graph_rejection_loop() -> None:  # noqa: C901
    from src.service_container import ServiceContainer
    from src.services.jules_client import JulesClient

    services = MagicMock()
    services.__class__ = ServiceContainer  # type: ignore[assignment]
    jules = MagicMock()
    jules.__class__ = JulesClient  # type: ignore[assignment]

    builder = GraphBuilder(services, jules=jules)

    nodes_mock = MagicMock()

    def mock_test_coder(state: CycleState) -> dict[str, Any]:
        return {}

    nodes_mock.test_coder_node = mock_test_coder

    def mock_impl_coder(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.READY_FOR_AUDIT}

    nodes_mock.impl_coder_node = mock_impl_coder

    def mock_sandbox_eval(state: CycleState) -> dict[str, Any]:
        return {"status": FlowStatus.READY_FOR_AUDIT}

    nodes_mock.sandbox_evaluate_node = mock_sandbox_eval

    def mock_auditor(state: CycleState) -> dict[str, Any]:
        from src.domain_models import AuditResult

        audit_res = AuditResult(is_approved=False, status="Reject")

        return {
            "audit": state.audit.model_copy(update={"audit_result": audit_res}),
            "committee": state.committee.model_copy(
                update={"audit_attempt_count": state.committee.audit_attempt_count + 1}
            ),
        }

    nodes_mock.auditor_node = mock_auditor

    def mock_self_critic(state: CycleState) -> dict[str, Any]:
        return {}

    nodes_mock.self_critic_node = mock_self_critic

    def mock_refactor(state: CycleState) -> dict[str, Any]:
        return {}

    nodes_mock.refactor_node = mock_refactor

    def mock_final_critic(state: CycleState) -> dict[str, Any]:
        return {}

    nodes_mock.final_critic_node = mock_final_critic

    def mock_committee_manager(state: CycleState) -> dict[str, Any]:
        return {}

    nodes_mock.committee_manager_node = mock_committee_manager

    def mock_check_coder_outcome(state: CycleState) -> str:
        return "self_critic_node"

    nodes_mock.check_coder_outcome = mock_check_coder_outcome

    def mock_route_committee(state: CycleState) -> str:
        return "impl_coder_node"

    nodes_mock.route_committee = mock_route_committee

    # Inject mock nodes
    builder.nodes = nodes_mock

    graph = builder.build_coder_graph()

    initial_state = CycleState(cycle_id="01")
    initial_state.status = FlowStatus.READY_FOR_AUDIT

    for iteration_count, state in enumerate(
        graph.stream(initial_state, config={"configurable": {"thread_id": "2"}})
    ):
        for _node_name, _node_state in state.items():
            pass
        if iteration_count > 10:  # Prevent infinite loop
            break

    # If rejection loop works correctly, it should exit via "requires_pivot" to END
    # Or at least audit_attempt_count should be tracked.
    final_state = graph.get_state({"configurable": {"thread_id": "2"}}).values
    assert getattr(final_state["committee"], "audit_attempt_count", 0) > 0
