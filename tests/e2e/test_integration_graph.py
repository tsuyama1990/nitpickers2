# mypy: ignore-errors
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain_models import ConflictRegistryItem
from src.graph import GraphBuilder
from src.service_container import ServiceContainer
from src.services.jules_client import JulesClient
from src.state import IntegrationState


@pytest.fixture
def repo_path(tmp_path: Path) -> Path:
    return tmp_path


@pytest.mark.asyncio
async def test_integration_graph_conflict_resolution(repo_path: Path) -> None:
    services = ServiceContainer.default()
    jules = MagicMock(spec=JulesClient)

    builder = GraphBuilder(services, jules=jules)

    mock_git_merge = AsyncMock()
    mock_git_merge.side_effect = [
        {
            "status": "conflict",
            "conflict_status": "conflict_detected",
            "unresolved_conflicts": [
                ConflictRegistryItem(
                    file_path="main.py",
                    conflict_markers=["<<<<<<<"],
                    resolution_attempts=0,
                    resolved=False,
                )
            ],
        },
        {"status": "success", "conflict_status": "conflict_resolved"},
    ]

    mock_master_integrator = AsyncMock()
    mock_master_integrator.return_value = {
        "unresolved_conflicts": [
            ConflictRegistryItem(
                file_path="main.py", conflict_markers=[], resolution_attempts=1, resolved=True
            )
        ]
    }

    mock_global_sandbox = AsyncMock()
    mock_global_sandbox.return_value = {"status": "pass"}

    builder.nodes.git_merge_node = mock_git_merge
    builder.nodes.master_integrator_node = mock_master_integrator
    builder.nodes.global_sandbox_node = mock_global_sandbox

    graph = builder.build_integration_graph()

    state = IntegrationState()

    config = {"configurable": {"thread_id": "test_thread"}}
    final_state = await graph.ainvoke(state, config=config)

    assert mock_git_merge.call_count == 2
    assert mock_master_integrator.call_count == 1
    assert mock_global_sandbox.call_count == 1

    assert final_state["unresolved_conflicts"][0].resolved is True
