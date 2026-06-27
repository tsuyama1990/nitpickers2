from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from src.cli import app
from src.domain_models import CycleManifest

# This file fulfills UAT.md Scenario ID: Pipeline_Orchestration_01 & 02
runner = CliRunner()


@pytest.fixture
def mock_manifest() -> MagicMock:
    manifest = MagicMock()
    manifest.cycles = [
        CycleManifest(id="01", status="planned"),
        CycleManifest(id="02", status="planned"),
    ]
    manifest.feature_branch = "integration"
    return manifest


@patch("src.services.workflow.StateManager")
@patch("src.services.workflow.AsyncDispatcher")
@patch("src.services.workflow.WorkflowService.verify_environment_and_observability", MagicMock())
def test_uat_full_5_phase_execution(
    mock_dispatcher_class: MagicMock, mock_state_manager_class: MagicMock, mock_manifest: MagicMock
) -> None:
    """
    Scenario ID: Pipeline_Orchestration_01 - Full 5-Phase Execution
    Verify that the CLI successfully orchestrates a complete run of all five phases
    for a multi-cycle project.
    """
    mock_mgr = mock_state_manager_class.return_value
    mock_mgr.load_manifest.return_value = mock_manifest

    mock_dispatcher = mock_dispatcher_class.return_value
    mock_dispatcher.resolve_dag.return_value = [mock_manifest.cycles]

    from collections.abc import Coroutine
    from typing import Any

    async def run_semaphore_mock(coro: Coroutine[Any, Any, Any]) -> Any:
        return await coro

    mock_dispatcher.run_with_semaphore = run_semaphore_mock

    with (
        patch(
            "src.services.workflow.WorkflowService._run_single_cycle", new_callable=AsyncMock
        ) as mock_single_cycle,
        patch("src.graph.GraphBuilder.build_integration_graph") as mock_build_integration,
        patch("src.graph.GraphBuilder.build_qa_graph") as mock_build_qa,
    ):
        mock_integration_graph = MagicMock()
        mock_integration_graph.ainvoke = AsyncMock(return_value={"conflict_status": "success"})
        mock_build_integration.return_value = mock_integration_graph

        mock_qa_graph = MagicMock()
        mock_qa_graph.ainvoke = AsyncMock(return_value={"status": "completed"})
        mock_build_qa.return_value = mock_qa_graph

        result = runner.invoke(app, ["run-pipeline", "--session", "test_session"])

        assert result.exit_code == 0
        assert mock_single_cycle.call_count == 2
        assert mock_integration_graph.ainvoke.call_count == 1
        assert mock_qa_graph.ainvoke.call_count == 1
        assert "Starting Full Pipeline Orchestration" in result.stdout
        assert "Phase 2: Parallel Coder Graph" in result.stdout
        assert "Phase 3: Integration Graph" in result.stdout
        assert "Phase 4: QA/UAT Graph" in result.stdout
        assert "Full Pipeline Execution Completed Successfully" in result.stdout


@patch("src.services.workflow.StateManager")
@patch("src.services.workflow.AsyncDispatcher")
@patch("src.services.workflow.WorkflowService.verify_environment_and_observability", MagicMock())
def test_uat_fail_fast_on_coder_phase_error(
    mock_dispatcher_class: MagicMock, mock_state_manager_class: MagicMock, mock_manifest: MagicMock
) -> None:
    """
    Scenario ID: Pipeline_Orchestration_02 - Fail-Fast on Coder Phase Error
    Verify that the orchestrator correctly halts the entire pipeline if a single parallel
    Coder Phase (Phase 2) cycle fails catastrophically.
    """
    mock_mgr = mock_state_manager_class.return_value
    mock_mgr.load_manifest.return_value = mock_manifest

    mock_dispatcher = mock_dispatcher_class.return_value
    mock_dispatcher.resolve_dag.return_value = [mock_manifest.cycles]

    from collections.abc import Coroutine
    from typing import Any

    async def run_semaphore_mock(coro: Coroutine[Any, Any, Any]) -> Any:
        return await coro

    mock_dispatcher.run_with_semaphore = run_semaphore_mock

    from typing import Any

    async def single_cycle_mock(cycle_id: str, **kwargs: Any) -> None:
        if cycle_id == "02":
            msg = "Intentional unrecoverable logic error"
            raise ValueError(msg)

    with (
        patch(
            "src.services.workflow.WorkflowService._run_single_cycle",
            new=AsyncMock(side_effect=single_cycle_mock),
        ),
        patch("src.graph.GraphBuilder.build_integration_graph") as mock_build_integration,
        patch("src.graph.GraphBuilder.build_qa_graph") as mock_build_qa,
    ):
        result = runner.invoke(app, ["run-pipeline", "--session", "test_session"])

        assert result.exit_code != 0
        assert mock_build_integration.call_count == 0
        assert mock_build_qa.call_count == 0
        assert "Pipeline halted due to Phase 2 failure" in result.stdout
        assert "Intentional unrecoverable logic error" in result.stdout
