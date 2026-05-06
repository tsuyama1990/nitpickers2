from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .enums import FlowStatus
from .graph_nodes import CycleNodes
from .interfaces import IGraphNodes
from .sandbox import SandboxRunner
from .service_container import ServiceContainer
from .services.jules_client import JulesClient
from .state import CycleState


class GraphBuilder:
    def __init__(
        self,
        services: ServiceContainer,
        sandbox: SandboxRunner,
        jules: JulesClient,
        nodes: IGraphNodes | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.jules = jules
        self.nodes: IGraphNodes = nodes or CycleNodes(self.sandbox, self.jules)

    async def cleanup(self) -> None:
        """Cleanup resources, specifically the sandbox."""
        if self.sandbox:
            await self.sandbox.cleanup()

    def _create_architect_graph(self) -> StateGraph[CycleState]:
        """Create the graph for the Architect phase (gen-cycles)."""
        if not self.nodes:
            msg = "Graph nodes are not initialized"
            raise ValueError(msg)
        if not getattr(self.nodes, "architect_session_node", None):
            msg = "architect_session_node is missing from Graph nodes"
            raise ValueError(msg)
        if not getattr(self.nodes, "architect_critic_node", None):
            msg = "architect_critic_node is missing from Graph nodes"
            raise ValueError(msg)

        workflow = StateGraph(CycleState)

        workflow.add_node("architect_session", self.nodes.architect_session_node)
        workflow.add_node("architect_critic", self.nodes.architect_critic_node)

        workflow.add_edge(START, "architect_session")

        # from architect_session to architect_critic only if successful
        workflow.add_conditional_edges(
            "architect_session",
            self.nodes.route_architect_session,
            {
                "architect_critic": "architect_critic",
                "end": END,
            },
        )

        # from architect_critic to END or back to architect_session
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

        required_nodes = [
            "impl_coder_node",
            "sandbox_evaluate_node",
            "auditor_node",
            "self_critic_node",
            "refactor_node",
            "final_critic_node",
        ]
        for n in required_nodes:
            if not getattr(self.nodes, n, None):
                msg = f"{n} is missing from Graph nodes"
                raise ValueError(msg)

        workflow = StateGraph(CycleState)

        from src.config import settings

        workflow.add_node("impl_coder_node", self.nodes.impl_coder_node)
        workflow.add_node(settings.node_sandbox_evaluate, self.nodes.sandbox_evaluate_node)
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
                settings.node_sandbox_evaluate: settings.node_sandbox_evaluate,
                FlowStatus.FAILED.value: END,
                FlowStatus.COMPLETED.value: END,
                "impl_coder_node": "impl_coder_node",
            },
        )

        workflow.add_edge("self_critic_node", settings.node_sandbox_evaluate)

        workflow.add_conditional_edges(
            settings.node_sandbox_evaluate,
            self.nodes.route_sandbox_evaluate,
            {
                "auditor": "auditor",
                "impl_coder_node": "impl_coder_node",
                "self_critic_node": "self_critic_node",
                "final_critic": "final_critic_node",
                "approve": END,
                "failed": END,
                "end": END,
            },
        )

        workflow.add_edge("auditor", "committee_manager_node")

        workflow.add_conditional_edges(
            "committee_manager_node",
            self.nodes.route_committee,  # type: ignore[attr-defined]
            {
                "impl_coder_node": "impl_coder_node",
                "next_auditor": "auditor",
                "refactor_node": "refactor_node",
                "final_critic": "final_critic_node",
            },
        )

        workflow.add_edge("refactor_node", settings.node_sandbox_evaluate)

        workflow.add_edge("final_critic_node", settings.node_sandbox_evaluate)

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
        workflow.add_node("qa_regression_sandbox_node", self.nodes.qa_regression_sandbox_node)

        workflow.add_node("uat_evaluate", self.nodes.uat_evaluate_node)
        workflow.add_node("ux_auditor", self.nodes.ux_auditor_node)

        workflow.add_edge(START, "uat_evaluate")

        workflow.add_conditional_edges(
            "uat_evaluate",
            lambda state: (
                "qa_auditor" if state.get("status") == FlowStatus.UAT_FAILED else "ux_auditor"
            ),
            {"qa_auditor": "qa_auditor", "ux_auditor": "ux_auditor"},
        )

        workflow.add_edge("ux_auditor", END)
        workflow.add_edge("qa_auditor", "qa_session")
        workflow.add_edge("qa_session", "qa_regression_sandbox_node")

        workflow.add_conditional_edges(
            "qa_regression_sandbox_node",
            lambda state: (
                "qa_session"
                if state.get("status") in {FlowStatus.FAILED, FlowStatus.TDD_FAILED}
                else "uat_evaluate"
            ),
            {"qa_session": "qa_session", "uat_evaluate": "uat_evaluate"},
        )

        return workflow

    def build_qa_graph(self) -> CompiledStateGraph[CycleState, Any, Any, Any]:
        return self._create_qa_graph().compile(checkpointer=MemorySaver())

    def _create_integration_graph(self) -> StateGraph["Any"]:
        """Create the graph for Phase 3: Integration."""
        from src.state import IntegrationState

        workflow = StateGraph(IntegrationState)

        workflow.add_node("git_merge_node", self.nodes.git_merge_node)
        workflow.add_node("master_integrator_node", self.nodes.master_integrator_node)
        workflow.add_node("global_sandbox_node", self.nodes.global_sandbox_node)
        workflow.add_node("integration_fixer_node", self.nodes.integration_fixer_node)

        workflow.add_edge(START, "git_merge_node")

        workflow.add_conditional_edges(
            "git_merge_node",
            self.nodes.route_merge,
            {
                "conflict": "master_integrator_node",
                "success": "global_sandbox_node",
            },
        )

        workflow.add_edge("master_integrator_node", "git_merge_node")

        workflow.add_conditional_edges(
            "global_sandbox_node",
            self.nodes.route_global_sandbox,
            {
                "failed": "integration_fixer_node",
                "pass": END,
            },
        )

        workflow.add_edge("integration_fixer_node", "global_sandbox_node")

        return workflow

    def build_integration_graph(self) -> CompiledStateGraph[Any, Any, Any, Any]:
        return self._create_integration_graph().compile(checkpointer=MemorySaver())
