"""Quality gate commands and refactoring result handling.

Split from workflow.py — part of WorkflowService decomposition.
"""

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any

from rich.console import Console

from src.config import settings
from src.process_runner import ProcessRunner
from src.service_container import ServiceContainer

console = Console()


class WorkflowQualityManager:
    """Quality gate commands and global refactoring result handling.

    Mixin class — depends on self being a WorkflowService instance
    that provides self.git, self.builder, self.services.
    """

    def _get_quality_gate_cmds(self) -> list[list[str]]:
        cmds: list[list[str]] = []
        if settings.sandbox.lint_check_cmd:
            cmds.append(settings.sandbox.lint_check_cmd)
        if settings.sandbox.type_check_cmd:
            cmds.append(settings.sandbox.type_check_cmd)
        if settings.sandbox.test_cmd:
            cmds.append(settings.sandbox.test_cmd.split())

        if not cmds:
            cmds = settings.sandbox.quality_gate_commands
        return cmds

    async def _handle_global_refactor_result(
        self,
        result: dict[str, Any],
        git: "Any",  # GitManager
    ) -> None:
        """Helper to handle the result of the global refactoring loop."""
        gr_res = result["global_refactor_result"]
        if not gr_res.refactorings_applied:
            return

        container = ServiceContainer.default()
        runner = (
            container.resolve(ProcessRunner) if hasattr(container, "resolve") else ProcessRunner()
        )
        cmds = self._get_quality_gate_cmds()

        # Execute quality gates in isolated temporary directories
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                # Copy current codebase to temp_dir to validate without affecting workspace yet
                # We want to run the tools in the temp dir
                temp_path = Path(temp_dir)

                # Exclude .git, .venv, etc when copying to save time and avoid issues
                def ignore_func(dir_path: str, contents: list[str]) -> list[str]:
                    return [c for c in contents if c in (".git", ".venv", "venv", "__pycache__")]

                await asyncio.to_thread(
                    shutil.copytree, Path.cwd(), temp_path / "workspace", ignore=ignore_func
                )
                workspace_dir = temp_path / "workspace"

                console.print(
                    "[cyan]Running final quality gates post-refactor in isolated sandbox...[/cyan]"
                )
                for cmd in cmds:
                    # This throws CalledProcessError if it fails
                    await runner.run_command(cmd, cwd=workspace_dir)

            # If we reached here, validations passed. Commit the changes in the actual workspace.
            status_output = await git.get_status()
            if status_output and status_output.strip():
                try:
                    await git.add_all()
                    await git.commit("Global refactoring applied.")
                    console.print("[green]Global refactoring successful and tests passed.[/green]")
                except Exception as commit_err:
                    console.print(
                        f"[bold red]Failed to commit global refactoring: {commit_err}[/bold red]"
                    )
                    await git.reset_hard()
        except Exception as e:
            console.print(
                f"[bold red]Quality gates failed after global refactoring: {e}[/bold red]"
            )
            console.print("[yellow]Reverting refactoring changes...[/yellow]")
            try:
                await git.reset_hard()
            except Exception as reset_err:
                console.print(f"[bold red]Failed to revert changes: {reset_err}[/bold red]")
            console.print(
                "[yellow]Refactoring changes reverted to maintain zero-trust validation.[/yellow]"
            )
