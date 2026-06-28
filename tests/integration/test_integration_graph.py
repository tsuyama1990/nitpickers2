# ruff: noqa: S607
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.graph import GraphBuilder
from src.service_container import ServiceContainer
from src.services.jules_client import JulesClient
from src.state import IntegrationState


@pytest.fixture
def repo_path(tmp_path: Path) -> Path:
    """Fixture to set up a real local bare git repository for testing."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # Initialize a git repository
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    # Create base commit
    base_file = repo / "utils.py"
    base_file.write_text("def my_func():\n    return 'base'\n")
    subprocess.run(["git", "add", "utils.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Base commit"], cwd=repo, check=True, capture_output=True
    )

    # Create Branch A
    subprocess.run(
        ["git", "checkout", "-b", "feature-a"], cwd=repo, check=True, capture_output=True
    )
    base_file.write_text("def my_func():\n    return 'branch-a'\n")
    subprocess.run(["git", "commit", "-am", "Feature A"], cwd=repo, check=True, capture_output=True)

    # Create Branch B
    subprocess.run(["git", "checkout", "master"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "checkout", "-b", "feature-b"], cwd=repo, check=True, capture_output=True
    )
    base_file.write_text("def my_func():\n    return 'branch-b'\n")
    subprocess.run(["git", "commit", "-am", "Feature B"], cwd=repo, check=True, capture_output=True)

    # Setup for merge
    subprocess.run(["git", "checkout", "master"], cwd=repo, check=True, capture_output=True)

    return repo


@pytest.fixture
def integration_graph() -> Any:
    jules = MagicMock(spec=JulesClient)

    container = ServiceContainer.default()
    builder = GraphBuilder(container, jules=jules)
    return builder.build_integration_graph()


@pytest.mark.asyncio
async def test_integration_graph_clean_merge(repo_path: Path, integration_graph: Any) -> None:
    """Test Scenario 1: Clean Merge"""
    # Create a clean branch to merge
    subprocess.run(
        ["git", "checkout", "-b", "clean-branch"], cwd=repo_path, check=True, capture_output=True
    )
    new_file = repo_path / "new_file.py"
    new_file.write_text("def new_func():\n    pass\n")
    subprocess.run(["git", "add", "new_file.py"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Clean branch"], cwd=repo_path, check=True, capture_output=True
    )
    subprocess.run(["git", "checkout", "master"], cwd=repo_path, check=True, capture_output=True)

    with (
        patch.object(settings.paths, "workspace_root", repo_path),
        patch("os.getcwd", return_value=str(repo_path)),
    ):
        # Provide the branch to merge
        state = IntegrationState(branches_to_merge=["clean-branch"])
        result = await integration_graph.ainvoke(
            state,
            config={"configurable": {"thread_id": "test_clean_merge"}},
        )

    assert result is not None
    # We expect the git_merge_node to actually merge the branch!
    # The dummy implementation currently hardcodes "merge_pr('1')" and will fail because branch "clean-branch" is expected.

    # Check if branch is merged
    # (If the real implementation was there, we'd see 'clean-branch' merged)
    git_log = subprocess.run(
        ["git", "log", "--oneline"], cwd=repo_path, check=True, capture_output=True, text=True
    ).stdout
    assert "Clean branch" in git_log


@pytest.mark.asyncio
async def test_integration_graph_conflict_resolution(
    repo_path: Path, integration_graph: Any
) -> None:
    """Test Scenario 2: Conflict Resolution via 3-Way Diff"""
    # Master is currently at Base. We merge feature-a, then feature-b to create a conflict.
    subprocess.run(["git", "merge", "feature-a"], cwd=repo_path, check=True, capture_output=True)

    # Now we simulate the graph merging feature-b
    with (
        patch.object(settings.paths, "workspace_root", repo_path),
        patch("os.getcwd", return_value=str(repo_path)),
        patch("src.nodes.master_integrator.JulesClient") as MockJules,
    ):
        # We need to mock the LLM inside master_integrator_node
        mock_jules_instance = MagicMock()
        MockJules.return_value = mock_jules_instance
        mock_jules_instance.create_master_integrator_session.return_value = "session_id"
        mock_jules_instance.send_message_to_session = AsyncMock(
            return_value='{"resolved_code": "def my_func():\\n    return \'resolved\'\\n"}'
        )

        state = IntegrationState(branches_to_merge=["feature-b"])
        result = await integration_graph.ainvoke(
            state,
            config={"configurable": {"thread_id": "test_conflict"}},
        )

        # The master integrator node should have been called
        assert mock_jules_instance.send_message_to_session.called

    assert result is not None
    # The conflict should be resolved
    assert len(result["unresolved_conflicts"]) > 0
    assert result["unresolved_conflicts"][0].resolved is True


@pytest.mark.asyncio
async def test_integration_graph_semantic_failure(repo_path: Path, integration_graph: Any) -> None:
    """Test Scenario 3: Post-Merge Semantic Failure Recovery"""
    # Simulate a merge that succeeds without conflicts but fails the sandbox
    with (
        patch.object(settings.paths, "workspace_root", repo_path),
        patch("os.getcwd", return_value=str(repo_path)),
        patch("src.nodes.integration_fixer.IntegrationFixerNodes.integration_fixer_node") as mock_fixer,
    ):
        # Mock the integration fixer node to resolve the issue
            mock_fixer.return_value = {"status": "success"}

            state = IntegrationState(branches_to_merge=["feature-a"])
            await integration_graph.ainvoke(
                state,
                config={"configurable": {"thread_id": "test_semantic"}},
            )

            assert mock_fixer.called
