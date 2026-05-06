import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Mocking modules that might fail to import due to missing dependencies
mock = MagicMock()
sys.modules["pydantic"] = mock
sys.modules["pydantic.fields"] = mock
sys.modules["pydantic_settings"] = mock
sys.modules["dotenv"] = mock
sys.modules["langchain_core"] = mock
sys.modules["langchain_core.callbacks"] = mock
sys.modules["rich"] = mock
sys.modules["rich.console"] = mock
sys.modules["rich.logging"] = mock
sys.modules["anyio"] = mock
sys.modules["google"] = mock
sys.modules["google.auth"] = mock
sys.modules["litellm"] = mock
sys.modules["e2b_code_interpreter"] = mock
sys.modules["langgraph"] = mock
sys.modules["langgraph.graph"] = mock
sys.modules["langgraph.prebuilt"] = mock

from src.services.project_setup.permission_manager import PermissionManager  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_large_dir_structure(base_path: Path, depth: int = 3, width: int = 5) -> None:
    if depth == 0:
        return
    base_path.mkdir(parents=True, exist_ok=True)
    for i in range(width):
        subdir = base_path / f"dir_{depth}_{i}"
        create_large_dir_structure(subdir, depth - 1, width)
        for j in range(width):
            file = base_path / f"file_{depth}_{i}_{j}.txt"
            file.write_text("test content")


async def run_benchmark() -> None:
    test_dir = Path("perf_test_dir")
    # For a benchmark script, synchronous Path methods are acceptable
    import shutil

    if test_dir.exists():  # noqa: ASYNC240
        shutil.rmtree(test_dir)

    logger.info("Creating large directory structure...")
    # depth=3, width=6 => ~1813 items
    create_large_dir_structure(test_dir, depth=3, width=6)

    manager = PermissionManager()

    with (
        patch("src.services.project_setup.permission_manager.os.chown"),
        patch("src.services.project_setup.permission_manager.os.chmod"),
        patch("src.services.project_setup.permission_manager.logger"),
        patch.dict(os.environ, {"HOST_UID": "1000", "HOST_GID": "1000"}),
    ):
        logger.info("Starting benchmark...")
        # Warm up
        await manager.fix_permissions(test_dir)

        # Measure
        total_duration: float = 0.0
        iterations = 5
        for i in range(iterations):
            start_time = time.perf_counter()
            await manager.fix_permissions(test_dir)
            end_time = time.perf_counter()
            duration = end_time - start_time
            logger.info(f"Iteration {i + 1}: {duration:.4f} seconds")
            total_duration += duration

    avg_duration = total_duration / iterations
    logger.info(f"Average benchmark took: {avg_duration:.4f} seconds")

    shutil.rmtree(test_dir)


if __name__ == "__main__":
    asyncio.run(run_benchmark())
