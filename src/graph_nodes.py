from typing import Any

from rich.console import Console

from src.enums import FlowStatus
from src.interfaces import IGraphNodes
from src.nodes import (
    ArchitectCriticNodes,
    ArchitectNodes,
    AuditorNodes,
    CoderCriticNodes,
    CoderNodes,
    CommitteeNodes,
    QaNodes,
    UatNodes,
    UxAuditorNodes,
    check_coder_outcome,
    route_architect_critic,
    route_architect_session,
    route_auditor,
    route_committee,
    route_final_critic,
    route_qa,
    route_sandbox_evaluate,
)
from src.nodes.global_refactor import GlobalRefactorNodes
from src.nodes.sandbox_evaluator import SandboxEvaluatorNodes
from src.sandbox import SandboxRunner
from src.services.audit_orchestrator import AuditOrchestrator
from src.services.git_ops import GitManager
from src.services.jules_client import JulesClient
from src.services.llm_reviewer import LLMReviewer
from src.state import CycleState

console = Console()


class CycleNodes(IGraphNodes):
    """
    Encapsulates the logic for each node in the AC-CDD workflow graph.
    """

    def __init__(
        self,
        sandbox_runner: SandboxRunner,
        jules_client: JulesClient,
        git_manager: GitManager | None = None,
    ) -> None:
        self.sandbox = sandbox_runner
        self.jules = jules_client

        from src.service_container import ServiceContainer

        container = ServiceContainer.default()

        self.git = git_manager or (
            container.resolve("git_manager") if hasattr(container, "resolve") else GitManager()
        )
        self.llm_reviewer = LLMReviewer(sandbox_runner=sandbox_runner)
        self.audit_orchestrator = AuditOrchestrator(jules_client, sandbox_runner)

        self._architect = ArchitectNodes(jules=self.jules, git=self.git)
        self._architect_critic = ArchitectCriticNodes(self.jules, git_manager=self.git)
        self._coder = CoderNodes(self.jules)
        self._coder_critic = CoderCriticNodes(self.jules)
        self._auditor = AuditorNodes(self.jules, self.git, self.llm_reviewer)
        self._committee = CommitteeNodes()
        self._uat = UatNodes(self.git)
        self._ux_auditor = UxAuditorNodes()
        self._sandbox_evaluator = SandboxEvaluatorNodes(git_manager=self.git)
        self._qa = QaNodes(self.jules, self.git, self.llm_reviewer)
        self._coder_critic = CoderCriticNodes(self.jules)

        # Dependency injection for Global Refactor
        from src.services.refactor_usecase import RefactorUsecase

        if hasattr(container, "resolve"):
            refactor_usecase = container.resolve(RefactorUsecase)
        else:
            refactor_usecase = RefactorUsecase(jules_client=self.jules)

        self._global_refactor = GlobalRefactorNodes(usecase=refactor_usecase)

    async def architect_session_node(self, state: CycleState) -> dict[str, Any]:
        return await self._architect.architect_session_node(state)

    async def architect_critic_node(self, state: CycleState) -> dict[str, Any]:
        return await self._architect_critic.architect_critic_node(state)

    async def _send_audit_feedback_to_session(
        self, session_id: str, feedback: str
    ) -> dict[str, Any] | None:
        return await self._architect.send_audit_feedback_to_session(session_id, feedback)

    async def test_coder_node(self, state: CycleState) -> dict[str, Any]:
        return await self._coder.test_coder_node(state)

    async def impl_coder_node(self, state: CycleState) -> dict[str, Any]:
        return await self._coder.impl_coder_node(state)

    async def auditor_node(self, state: CycleState) -> dict[str, Any]:
        return await self._auditor.auditor_node(state)

    async def committee_manager_node(self, state: CycleState) -> dict[str, Any]:
        return await self._committee.committee_manager_node(state)

    async def uat_evaluate_node(self, state: CycleState) -> dict[str, Any]:
        return await self._uat.uat_evaluate_node(state)

    async def ux_auditor_node(self, state: CycleState) -> dict[str, Any]:
        return await self._ux_auditor.ux_auditor_node(state)

    async def sandbox_evaluate_node(self, state: CycleState) -> dict[str, Any]:
        return await self._sandbox_evaluator.sandbox_evaluate_node(state)

    async def global_refactor_node(self, state: CycleState) -> dict[str, Any]:
        return await self._global_refactor.global_refactor_node(state)

    async def refactor_node(self, state: CycleState) -> dict[str, Any]:
        return await self.global_refactor_node(state)

    async def self_critic_node(self, state: CycleState) -> dict[str, Any]:
        return await self._coder_critic.coder_critic_node(state)

    async def final_critic_node(self, state: CycleState) -> dict[str, Any]:
        return await self._coder_critic.coder_critic_node(state)

    async def git_merge_node(self, state: "Any") -> dict[str, Any]:
        from src.services.git_ops import GitManager

        try:
            gm = GitManager()
            await gm.merge_pr("1")
        except Exception as e:
            return {"error": str(e), "status": "conflict"}
        else:
            return {"status": "success"}

    async def master_integrator_node(self, state: "Any") -> dict[str, Any]:
        from src.nodes.master_integrator import MasterIntegratorNodes
        from src.services.jules_client import JulesClient

        integrator = MasterIntegratorNodes(jules_client=JulesClient())
        return await integrator.master_integrator_node(state)

    async def integration_fixer_node(self, state: "Any") -> dict[str, Any]:
        from src.nodes.integration_fixer import IntegrationFixerNodes
        from src.services.jules_client import JulesClient

        fixer = IntegrationFixerNodes(jules_client=JulesClient())
        return await fixer.integration_fixer_node(state)

    async def global_sandbox_node(self, state: "Any") -> dict[str, Any]:
        from src.nodes.sandbox_evaluator import SandboxEvaluatorNodes

        sandbox = SandboxEvaluatorNodes()
        # Mocking CycleState to fit sandbox_evaluate_node expectation
        return await sandbox.sandbox_evaluate_node(CycleState(cycle_id="00"))

    async def qa_regression_sandbox_node(self, state: CycleState) -> dict[str, Any]:
        from src.nodes.sandbox_evaluator import SandboxEvaluatorNodes

        sandbox = SandboxEvaluatorNodes()
        return await sandbox.sandbox_evaluate_node(state)

    def route_merge(self, state: "Any") -> str:
        from src.nodes.routers import route_merge

        return route_merge(state)

    def route_global_sandbox(self, state: "Any") -> str:
        from src.nodes.routers import route_global_sandbox

        return route_global_sandbox(state)

    def check_coder_outcome(self, state: CycleState) -> str:
        return check_coder_outcome(state)

    def route_architect_critic(self, state: CycleState) -> str:
        return route_architect_critic(state)

    def route_architect_session(self, state: CycleState) -> str:
        return route_architect_session(state)

    def route_committee(self, state: CycleState) -> str:
        return route_committee(state)

    def route_auditor(self, state: CycleState) -> str:
        return route_auditor(state)

    def route_final_critic(self, state: CycleState) -> str:
        return route_final_critic(state)

    def route_sandbox_evaluate(self, state: CycleState) -> str:
        return route_sandbox_evaluate(state)

    def route_coder_critic(self, state: CycleState) -> str:
        if state.status == FlowStatus.REJECTED:
            return "coder_session"
        return "sandbox_evaluate"

    async def qa_session_node(self, state: CycleState) -> dict[str, Any]:
        return await self._qa.qa_session_node(state)

    async def qa_auditor_node(self, state: CycleState) -> dict[str, Any]:
        return await self._qa.qa_auditor_node(state)

    def route_qa(self, state: CycleState) -> str:
        return route_qa(state)

    async def coder_critic_node(self, state: CycleState) -> dict[str, Any]:
        return await self._coder_critic.coder_critic_node(state)
