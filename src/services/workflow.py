"""WorkflowService — Facade composed from specialized workflow modules.

This file exists solely to preserve the import path:
    from src.services.workflow import WorkflowService

All logic has been decomposed into 6 focused modules under src/services/.
"""

import asyncio
from typing import Any

from src.graph import GraphBuilder
from src.service_container import ServiceContainer
from src.services.async_dispatcher import (
    AsyncDispatcher,  # noqa: F401 — re-exported for test patches
)
from src.services.git_ops import GitManager
from src.services.workflow_archive import WorkflowArchiver
from src.services.workflow_cycle import WorkflowCycleExecutor
from src.services.workflow_failure import WorkflowFailureHandler
from src.services.workflow_orchestrator import WorkflowOrchestrator
from src.services.workflow_quality import WorkflowQualityManager
from src.services.workflow_session import WorkflowSessionManager
from src.state_manager import StateManager  # noqa: F401 — re-exported for test patches


class WorkflowService(
    WorkflowOrchestrator,
    WorkflowCycleExecutor,
    WorkflowSessionManager,
    WorkflowArchiver,
    WorkflowFailureHandler,
    WorkflowQualityManager,
):
    """Facade class that composes specialized workflow modules via MRO.

    All public API methods (run_gen_cycles, run_cycle, run_full_pipeline, etc.)
    are inherited from the respective base classes. __init__ is defined here
    to set shared instance attributes accessed by all base classes.

    Usage:
        from src.services.workflow import WorkflowService
        service = WorkflowService()
        await service.run_gen_cycles(5, "session-1")
    """

    def __init__(self, services: ServiceContainer | None = None) -> None:
        self.services = services or ServiceContainer.default()
        self.builder = GraphBuilder(self.services, self.services.jules)
        self.git = GitManager()
        self._background_tasks: set[asyncio.Task[Any]] = set()
