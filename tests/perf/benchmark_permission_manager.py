import os
import time
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys

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

from src.services.project_setup.permission_manager import PermissionManager

def create_large_dir_structure(base_path, depth=3, width=5):
    if depth == 0:
        return
    base_path.mkdir(parents=True, exist_ok=True)
    for i in range(width):
        subdir = base_path / f"dir_{depth}_{i}"
        create_large_dir_structure(subdir, depth - 1, width)
        for j in range(width):
            file = base_path / f"file_{depth}_{i}_{j}.txt"
            file.write_text("test content")

async def run_benchmark():
    test_dir = Path("perf_test_dir")
    if test_dir.exists():
        import shutil
        shutil.rmtree(test_dir)

    print("Creating large directory structure...")
    # depth=3, width=6 => ~1813 items
    create_large_dir_structure(test_dir, depth=3, width=6)

    manager = PermissionManager()

    with patch("src.services.project_setup.permission_manager.os.chown"), \
         patch("src.services.project_setup.permission_manager.os.chmod"), \
         patch("src.services.project_setup.permission_manager.logger"), \
         patch.dict(os.environ, {"HOST_UID": "1000", "HOST_GID": "1000"}):

        print("Starting benchmark...")
        # Warm up
        await manager.fix_permissions(test_dir)

        # Measure
        total_duration = 0
        iterations = 5
        for i in range(iterations):
            start_time = time.perf_counter()
            await manager.fix_permissions(test_dir)
            end_time = time.perf_counter()
            duration = end_time - start_time
            print(f"Iteration {i+1}: {duration:.4f} seconds")
            total_duration += duration

    avg_duration = total_duration / iterations
    print(f"Average benchmark took: {avg_duration:.4f} seconds")

    import shutil
    shutil.rmtree(test_dir)

if __name__ == "__main__":
    asyncio.run(run_benchmark())
