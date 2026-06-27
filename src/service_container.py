from dataclasses import dataclass
from typing import Any

from src.services.artifacts import ArtifactManager
from src.services.contracts import ContractManager
from src.services.file_ops import FilePatcher
from src.services.git_ops import GitManager
from src.services.jules_client import JulesClient
from src.services.llm_reviewer import LLMReviewer


@dataclass
class ServiceContainer:
    file_patcher: FilePatcher
    contract_manager: ContractManager
    artifact_manager: ArtifactManager
    jules: Any
    reviewer: LLMReviewer | None = None
    git: GitManager | None = None

    @classmethod
    def default(cls) -> "ServiceContainer":
        return cls(
            file_patcher=FilePatcher(),
            contract_manager=ContractManager(),
            artifact_manager=ArtifactManager(),
            jules=JulesClient(),
            reviewer=LLMReviewer(),
            git=GitManager(),
        )
