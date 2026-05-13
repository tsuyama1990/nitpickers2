import os
import shutil
import subprocess
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from src.cli import app


@pytest.fixture
def temp_project_dir() -> Generator[str, None, None]:
    # Create a temporary directory
    temp_dir = tempfile.mkdtemp()

    # Store the original current working directory
    original_cwd = Path.cwd()

    # Ensure there's a dummy template to copy, or just create minimal files
    os.chdir(temp_dir)
    Path("src").mkdir(parents=True, exist_ok=True)
    with Path("src/dummy.py").open("w") as f:
        f.write("def dummy():\n    pass\n")

    with Path(".env").open("w") as f:
        pass  # Empty .env

    # Initialize git repo locally so git commands don't fail
    git_exec = shutil.which("git") or "git"

    subprocess.run([git_exec, "init"], check=True)
    subprocess.run([git_exec, "add", "."], check=True)
    subprocess.run([git_exec, "config", "user.email", "test@example.com"], check=True)
    subprocess.run([git_exec, "config", "user.name", "Test User"], check=True)
    subprocess.run([git_exec, "commit", "-m", "Initial commit"], check=True)

    yield temp_dir

    # Teardown
    os.chdir(original_cwd)
    shutil.rmtree(temp_dir)


@pytest.mark.live
def test_live_full_cycle(temp_project_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()

    # Ensure required live API keys are present
    assert os.getenv("OPENROUTER_API_KEY")
    assert os.getenv("JULES_API_KEY")
    assert os.getenv("E2B_API_KEY")
    assert os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITHUB_TOKEN")

    # Run nitpick init
    result_init = runner.invoke(app, ["init", "--skip-git-config"])
    assert result_init.exit_code == 0

    # Create ALL_SPEC.md for gen-cycles to use
    Path("dev_documents").mkdir(parents=True, exist_ok=True)
    with Path("dev_documents/ALL_SPEC.md").open("w") as f:
        f.write("# Dummy Spec\n\nCreate a function that adds two numbers.\n")

    # Run nitpick gen-cycles, limited to 1 cycle for speed/cost
    result_gen = runner.invoke(
        app, ["gen-cycles", "--n", "1", "--session", "test-live-session", "--auto"]
    )

    # Ensure it ran without Python errors
    assert result_gen.exit_code == 0
    assert (
        "Full Pipeline Execution Completed Successfully" in result_gen.stdout
        or "Auto-Running All Cycles" in result_gen.stdout
    )
