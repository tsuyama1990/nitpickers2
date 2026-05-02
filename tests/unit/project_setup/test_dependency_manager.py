import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.services.project_setup.dependency_manager import DependencyManager

@pytest.fixture
def dependency_manager():
    with patch("src.services.project_setup.dependency_manager.ProcessRunner"), \
         patch("src.services.project_setup.dependency_manager.GitManager"):
        yield DependencyManager()

@pytest.mark.asyncio
async def test_sync_dependencies_success(dependency_manager):
    # Mock runner.run_command to return success
    dependency_manager.runner.run_command = AsyncMock(side_effect=[
        (b"output", b"", 0, None),  # uv sync
        (b"ruff 0.1.0", b"", 0, None),  # ruff --version
        (b"mypy 1.0.0", b"", 0, None),  # mypy --version
    ])

    await dependency_manager.sync_dependencies()

    assert dependency_manager.runner.run_command.call_count == 3
    dependency_manager.runner.run_command.assert_any_call(["uv", "sync", "--dev"], check=True)

@pytest.mark.asyncio
async def test_sync_dependencies_exception_path(dependency_manager):
    # Mock runner.run_command to raise an exception during uv sync
    dependency_manager.runner.run_command = AsyncMock(side_effect=Exception("Sync failed"))

    with patch("src.services.project_setup.dependency_manager.logger") as mock_logger:
        await dependency_manager.sync_dependencies()

        mock_logger.warning.assert_called_with("[ProjectManager] Dependency sync failed: Sync failed")

@pytest.mark.asyncio
async def test_sync_dependencies_install_missing_linters(dependency_manager):
    # Mock runner.run_command: uv sync success, ruff fails, mypy success, uv add success
    dependency_manager.runner.run_command = AsyncMock(side_effect=[
        (b"output", b"", 0, None),  # uv sync
        (b"", b"error", 1, None),   # ruff --version fails
        (b"mypy 1.0.0", b"", 0, None),  # mypy --version
        (b"output", b"", 0, None),  # uv add
    ])

    await dependency_manager.sync_dependencies()

    assert dependency_manager.runner.run_command.call_count == 4
    dependency_manager.runner.run_command.assert_any_call(["uv", "add", "--dev", "ruff", "mypy"], check=True)

@pytest.mark.asyncio
async def test_sync_dependencies_uv_add_fails(dependency_manager):
    # Mock runner.run_command: uv sync success, ruff fails, mypy success, uv add fails
    dependency_manager.runner.run_command = AsyncMock(side_effect=[
        (b"output", b"", 0, None),  # uv sync
        (b"", b"error", 1, None),   # ruff --version fails
        (b"mypy 1.0.0", b"", 0, None),  # mypy --version
        Exception("uv add failed"),  # uv add fails
    ])

    with patch("src.services.project_setup.dependency_manager.logger") as mock_logger:
        await dependency_manager.sync_dependencies()

        mock_logger.warning.assert_called_with("[ProjectManager] Dependency sync failed: uv add failed")
