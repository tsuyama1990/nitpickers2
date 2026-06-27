from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .enums import FlowStatus
from .graph_nodes import CycleNodes
from .service_container import ServiceContainer
from .state import CycleState


class GraphBuilder:
    def __init__(
        self,
        services: ServiceContainer,
        jules: Any,
        nodes: Any | None = None,
    ) -> None:
        self.jules = jules
        self.nodes: Any = nodes or CycleNodes(self.jules)

    def _create_architect_graph(self) -> StateGraph[CycleState]:
        """Create the graph for the Architect phase (gen-cycles)."""
        if not self.nodes:
            msg = "Graph nodes are not initialized"
            raise ValueError(msg)

        workflow = StateGraph(CycleState)

        workflow.add_node("architect_session", self.nodes.architect_session_node)
        workflow.add_node("architect_critic", self.nodes.architect_critic_node)

        workflow.add_edge(START, "architect_session")

        workflow.add_conditional_edges(
            "architect_session",
            self.nodes.route_architect_session,
            {
                "architect_critic": "architect_critic",
                "end": END,
            },
        )

        workflow.add_conditional_edges(
            "architect_critic",
            self.nodes.route_architect_critic,
            {
                "architect_session": "architect_session",
                "end": END,
            },
        )

        return workflow

    def _create_coder_graph(self) -> StateGraph[CycleState]:
        """Create the graph for the Coder/Auditor phase (run-cycle)."""
        if not self.nodes:
            msg = "Graph nodes are not initialized"
            raise ValueError(msg)

        workflow = StateGraph(CycleState)

        workflow.add_node("impl_coder_node", self.nodes.impl_coder_node)
        workflow.add_node("auditor", self.nodes.auditor_node)
        workflow.add_node("committee_manager_node", self.nodes.committee_manager_node)
        workflow.add_node("self_critic_node", self.nodes.self_critic_node)
        workflow.add_node("refactor_node", self.nodes.refactor_node)
        workflow.add_node("final_critic_node", self.nodes.final_critic_node)

        workflow.add_edge(START, "impl_coder_node")

        workflow.add_conditional_edges(
            "impl_coder_node",
            self.nodes.check_coder_outcome,
            {
                "self_critic_node": "self_critic_node",
                FlowStatus.FAILED.value: END,
                FlowStatus.COMPLETED.value: END,
                "impl_coder_node": "impl_coder_node",
            },
        )

        workflow.add_edge("self_critic_node", "auditor")
        workflow.add_edge("auditor", "committee_manager_node")

        workflow.add_conditional_edges(
            "committee_manager_node",
            self.nodes.route_committee,
            {
                "impl_coder_node": "impl_coder_node",
                "next_auditor": "auditor",
                "refactor_node": "refactor_node",
                "final_critic": "final_critic_node",
            },
        )

        workflow.add_edge("refactor_node", "auditor")
        workflow.add_edge("final_critic_node", END)

        return workflow

    def build_architect_graph(self) -> CompiledStateGraph[CycleState, Any, Any, Any]:
        return self._create_architect_graph().compile(checkpointer=MemorySaver())

    def build_coder_graph(self) -> CompiledStateGraph[CycleState, Any, Any, Any]:
        return self._create_coder_graph().compile(checkpointer=MemorySaver())

    def _create_qa_graph(self) -> StateGraph[CycleState]:
        """Create the graph for the QA/Tutorial generation phase."""
        workflow = StateGraph(CycleState)

        workflow.add_node("qa_session", self.nodes.qa_session_node)
        workflow.add_node("qa_auditor", self.nodes.qa_auditor_node)
        workflow.add_node("uat_evaluate", self.nodes.uat_evaluate_node)
        workflow.add_node("ux_auditor", self.nodes.ux_auditor_node)

        workflow.add_edge(START, "uat_evaluate")

        workflow.add_conditional_edges(
            "uat_evaluate",
            self.nodes.route_qa,
            {"qa_auditor": "qa_auditor", "ux_auditor": "ux_auditor", "end": END},
        )

        workflow.add_edge("ux_auditor", END)
        workflow.add_edge("qa_auditor", "qa_session")
        workflow.add_edge("qa_session", END)

        return workflow

    def build_qa_graph(self) -> CompiledStateGraph[CycleState, Any, Any, Any]:
        return self._create_qa_graph().compile(checkpointer=MemorySaver())

    def _create_integration_graph(self) -> StateGraph["Any"]:
        """Create the graph for Phase 3: Integration."""
        from src.state import IntegrationState

        workflow = StateGraph(IntegrationState)

        workflow.add_node("git_merge_node", self.nodes.git_merge_node)
        workflow.add_node("master_integrator_node", self.nodes.master_integrator_node)
        workflow.add_node("integration_fixer_node", self.nodes.integration_fixer_node)

        workflow.add_edge(START, "git_merge_node")

        # Conflict → master integrator (retry loop), Success → integration fixer
        workflow.add_conditional_edges(
            "git_merge_node",
            self.nodes.route_merge,
            {
                "conflict": "master_integrator_node",
                "success": "integration_fixer_node",
            },
        )

        workflow.add_edge("master_integrator_node", "git_merge_node")
        workflow.add_edge("integration_fixer_node", END)

        return workflow

    def build_integration_graph(self) -> CompiledStateGraph[Any, Any, Any, Any]:
        return self._create_integration_graph().compile(checkpointer=MemorySaver())
