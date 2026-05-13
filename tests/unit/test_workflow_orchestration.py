from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain_models.manifest import CycleManifest
from src.services.workflow import WorkflowService


@pytest.fixture
def mock_manifest() -> MagicMock:
    manifest = MagicMock()
    manifest.cycles = [
        CycleManifest(id="01", status="planned"),
        CycleManifest(id="02", status="planned"),
    ]
    manifest.feature_branch = "integration"
    return manifest


@pytest.fixture
def workflow_service() -> Generator[WorkflowService, None, None]:
    with patch("src.services.workflow.EnvironmentValidator.verify"):
        service = WorkflowService()
        yield service


@pytest.mark.asyncio
@patch("src.services.workflow.StateManager")
@patch("src.services.workflow.AsyncDispatcher")
async def test_run_full_pipeline_success(
    mock_dispatcher_class: MagicMock,
    mock_state_manager_class: MagicMock,
    workflow_service: WorkflowService,
    mock_manifest: MagicMock,
) -> None:
    mock_mgr = mock_state_manager_class.return_value
    mock_mgr.load_manifest.return_value = mock_manifest

    mock_dispatcher = mock_dispatcher_class.return_value
    mock_dispatcher.resolve_dag.return_value = [mock_manifest.cycles]

    async def mock_execute_batches(
        batches: list[list[Any]], task_func: Callable[[Any], Any]
    ) -> list[Any]:
        results = []
        for batch in batches:
            for item in batch:
                res = await task_func(item)
                results.append(res if res is not None else True)
        return results

    mock_dispatcher.execute_batches = AsyncMock(side_effect=mock_execute_batches)

    # Await the AsyncMock so it matches the expected coroutine structure
    workflow_service._run_single_cycle = AsyncMock(return_value=True)  # type: ignore[method-assign]

    mock_integration_graph = MagicMock()
    mock_integration_graph.ainvoke = AsyncMock(return_value={"conflict_status": "success"})

    mock_qa_graph = MagicMock()
    mock_qa_graph.ainvoke = AsyncMock(return_value={"status": "completed"})

    workflow_service.builder.build_integration_graph = MagicMock(  # type: ignore[method-assign]
        return_value=mock_integration_graph
    )
    workflow_service.builder.build_qa_graph = MagicMock(return_value=mock_qa_graph)  # type: ignore[method-assign]

    await workflow_service.run_full_pipeline(project_session_id="test_session")

    assert workflow_service._run_single_cycle.call_count == 2
    mock_integration_graph.ainvoke.assert_called_once()
    mock_qa_graph.ainvoke.assert_called_once()


@pytest.mark.asyncio
@patch("src.services.workflow.StateManager")
@patch("src.services.workflow.AsyncDispatcher")
async def test_run_full_pipeline_fail_fast_on_coder(
    mock_dispatcher_class: MagicMock,
    mock_state_manager_class: MagicMock,
    workflow_service: WorkflowService,
    mock_manifest: MagicMock,
) -> None:
    mock_mgr = mock_state_manager_class.return_value
    mock_mgr.load_manifest.return_value = mock_manifest

    mock_dispatcher = mock_dispatcher_class.return_value
    mock_dispatcher.resolve_dag.return_value = [mock_manifest.cycles]

    async def mock_execute_batches(
        batches: list[list[Any]], task_func: Callable[[Any], Any]
    ) -> list[Any]:
        results = []
        for batch in batches:
            for item in batch:
                try:
                    res = await task_func(item)
                    results.append(res if res is not None else True)
                except Exception as e:
                    results.append(e)
        return results

    mock_dispatcher.execute_batches = AsyncMock(side_effect=mock_execute_batches)

    async def single_cycle_mock(cycle_id: str, **kwargs: Any) -> bool:
        if cycle_id == "02":
            msg = "Intentional coder failure"
            raise ValueError(msg)
        return True

    workflow_service._run_single_cycle = AsyncMock(side_effect=single_cycle_mock)  # type: ignore[method-assign]

    mock_integration_graph = MagicMock()
    mock_qa_graph = MagicMock()
    workflow_service.builder.build_integration_graph = MagicMock(  # type: ignore[method-assign]
        return_value=mock_integration_graph
    )
    workflow_service.builder.build_qa_graph = MagicMock(return_value=mock_qa_graph)  # type: ignore[method-assign]

    with pytest.raises(RuntimeError) as exc_info:
        await workflow_service.run_full_pipeline(project_session_id="test_session")

    assert "One or more cycles failed" in str(exc_info.value)
    assert workflow_service._run_single_cycle.call_count == 2
    mock_integration_graph.ainvoke.assert_not_called()
    mock_qa_graph.ainvoke.assert_not_called()


@pytest.mark.asyncio
@patch("src.services.workflow.StateManager")
@patch("src.services.workflow.AsyncDispatcher")
async def test_run_full_pipeline_fail_on_integration(
    mock_dispatcher_class: MagicMock,
    mock_state_manager_class: MagicMock,
    workflow_service: WorkflowService,
    mock_manifest: MagicMock,
) -> None:
    mock_mgr = mock_state_manager_class.return_value
    mock_mgr.load_manifest.return_value = mock_manifest

    mock_dispatcher = mock_dispatcher_class.return_value
    mock_dispatcher.resolve_dag.return_value = [mock_manifest.cycles]

    async def mock_execute_batches(
        batches: list[list[Any]], task_func: Callable[[Any], Any]
    ) -> list[Any]:
        results = []
        for batch in batches:
            for item in batch:
                res = await task_func(item)
                results.append(res if res is not None else True)
        return results

    mock_dispatcher.execute_batches = AsyncMock(side_effect=mock_execute_batches)

    workflow_service._run_single_cycle = AsyncMock(return_value=True)  # type: ignore[method-assign]

    mock_integration_graph = MagicMock()
    mock_integration_graph.ainvoke = AsyncMock(return_value={"conflict_status": "failed"})

    mock_qa_graph = MagicMock()

    workflow_service.builder.build_integration_graph = MagicMock(  # type: ignore[method-assign]
        return_value=mock_integration_graph
    )
    workflow_service.builder.build_qa_graph = MagicMock(return_value=mock_qa_graph)  # type: ignore[method-assign]

    with pytest.raises(RuntimeError) as exc_info:
        await workflow_service.run_full_pipeline(project_session_id="test_session")

    assert "Integration Phase Failed" in str(exc_info.value)
    assert workflow_service._run_single_cycle.call_count == 2
    mock_integration_graph.ainvoke.assert_called_once()
    mock_qa_graph.ainvoke.assert_not_called()
