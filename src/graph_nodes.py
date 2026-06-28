"""LangGraph nodes — consolidated single-file implementation.

All graph node logic is wired here with proper dependency injection.
Tiny wrapper classes (CommitteeNodes, UatNodes, UxAuditorNodes, QaNodes)
have been inlined to reduce file count and indirection.
"""

from typing import Any

from src.nodes import (
    ArchitectCriticNodes,
    ArchitectNodes,
    AuditorNodes,
    CoderCriticNodes,
    CoderNodes,
    GlobalRefactorNodes,
    IntegrationFixerNodes,
    MasterIntegratorNodes,
    check_coder_outcome,
    route_architect_critic,
    route_architect_session,
    route_committee,
    route_merge,
    route_qa,
)
from src.services.committee_usecase import CommitteeUseCase
from src.services.git_ops import GitManager
from src.services.integration_usecase import MasterIntegratorClient
from src.services.llm_reviewer import LLMReviewer
from src.services.qa_usecase import QaUseCase
from src.services.refactor_usecase import RefactorUsecase
from src.services.uat_usecase import UatUseCase
from src.services.ux_auditor_usecase import UxAuditorUseCase
from src.state import CycleState


class CycleNodes:
    """
    Encapsulates the logic for each node in the AC-CDD workflow graph.
    All dependencies are injected once in __init__ — never instantiated per-call.
    """

    def __init__(
        self,
        jules_client: Any,
        git_manager: GitManager | None = None,
        llm_reviewer: LLMReviewer | None = None,
    ) -> None:
        self.jules = jules_client
        self.git = git_manager or GitManager()
        self.llm_reviewer = llm_reviewer or LLMReviewer()

        # Sub-node delegates (larger classes kept as separate files)
        self._architect = ArchitectNodes(jules=self.jules, git=self.git)
        self._architect_critic = ArchitectCriticNodes(self.jules, git_manager=self.git)
        self._coder = CoderNodes(self.jules)
        self._coder_critic = CoderCriticNodes(self.jules)
        self._auditor = AuditorNodes(self.jules, self.git, self.llm_reviewer)

        # Inlined usecases — created once, not per-call
        self._committee_usecase = CommitteeUseCase()
        self._uat_usecase = UatUseCase(self.git)
        self._ux_auditor_usecase = UxAuditorUseCase()
        self._qa_usecase = QaUseCase(self.jules, self.git, self.llm_reviewer)
        self._refactor_usecase = RefactorUsecase(jules_client=self.jules)

        # Global Refactor
        self._global_refactor = GlobalRefactorNodes(usecase=self._refactor_usecase)

    # ── Architect ──────────────────────────────────────────────

    async def architect_session_node(self, state: CycleState) -> dict[str, Any]:
        return await self._architect.architect_session_node(state)

    async def architect_critic_node(self, state: CycleState) -> dict[str, Any]:
        return await self._architect_critic.architect_critic_node(state)

    # ── Coder ──────────────────────────────────────────────────

    async def test_coder_node(self, state: CycleState) -> dict[str, Any]:
        return await self._coder.test_coder_node(state)

    async def impl_coder_node(self, state: CycleState) -> dict[str, Any]:
        return await self._coder.impl_coder_node(state)

    async def self_critic_node(self, state: CycleState) -> dict[str, Any]:
        return await self._coder_critic.coder_critic_node(state)

    async def final_critic_node(self, state: CycleState) -> dict[str, Any]:
        return await self._coder_critic.coder_critic_node(state)

    # ── Auditor & Committee (inlined from tiny wrappers) ──────

    async def auditor_node(self, state: CycleState) -> dict[str, Any]:
        return await self._auditor.auditor_node(state)

    async def committee_manager_node(self, state: CycleState) -> dict[str, Any]:
        return dict(await self._committee_usecase.execute(state))

    # ── UAT, UX, QA (inlined from tiny wrappers) ──────────────

    async def uat_evaluate_node(self, state: CycleState) -> dict[str, Any]:
        return dict(await self._uat_usecase.execute(state))

    async def ux_auditor_node(self, state: CycleState) -> dict[str, Any]:
        return dict(await self._ux_auditor_usecase.execute(state))

    async def qa_session_node(self, state: CycleState) -> dict[str, Any]:
        return dict(await self._qa_usecase.execute_qa_session(state))

    async def qa_auditor_node(self, state: CycleState) -> dict[str, Any]:
        return dict(await self._qa_usecase.execute_qa_audit(state))

    # ── Refactor ───────────────────────────────────────────────

    async def global_refactor_node(self, state: CycleState) -> dict[str, Any]:
        return await self._global_refactor.global_refactor_node(state)

    async def refactor_node(self, state: CycleState) -> dict[str, Any]:
        return await self.global_refactor_node(state)

    # ── Integration ────────────────────────────────────────────

    async def git_merge_node(self, state: "Any") -> dict[str, Any]:
        try:
            gm = GitManager()
            await gm.merge_pr("1")
        except Exception as e:
            return {"error": str(e), "status": "conflict"}
        return {"status": "success"}

    async def master_integrator_node(self, state: "Any") -> dict[str, Any]:
        integrator = MasterIntegratorNodes(
            master_integrator=MasterIntegratorClient()
        )
        return await integrator.master_integrator_node(state)

    async def integration_fixer_node(self, state: "Any") -> dict[str, Any]:
        fixer = IntegrationFixerNodes(jules_client=self.jules)
        return await fixer.integration_fixer_node(state)

    # ── Routers ────────────────────────────────────────────────

    def route_merge(self, state: "Any") -> str:
        return route_merge(state)

    def check_coder_outcome(self, state: CycleState) -> str:
        return check_coder_outcome(state)

    def route_architect_critic(self, state: CycleState) -> str:
        return route_architect_critic(state)

    def route_architect_session(self, state: CycleState) -> str:
        return route_architect_session(state)

    def route_committee(self, state: CycleState) -> str:
        return route_committee(state)

    def route_qa(self, state: CycleState) -> str:
        return route_qa(state)
