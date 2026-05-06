from typing import Any

from rich.console import Console

from src.config import settings
from src.contracts.e2b_executor import E2BExecutorService
from src.domain_models.verification_schema import StructuralGateReport, VerificationResult
from src.enums import FlowStatus
from src.process_runner import ProcessRunner
from src.services.e2b_executor import E2BExecutorServiceImpl
from src.services.git_ops import GitManager, workspace_lock
from src.state import CycleState

console = Console()


class SandboxEvaluatorNodes:
    """
    Evaluates the code and tests using the E2B Sandbox for Agentic TDD loop.
    """

    def __init__(
        self,
        executor: E2BExecutorService | None = None,
        process_runner: ProcessRunner | None = None,
        git_manager: GitManager | None = None,
    ) -> None:
        self.executor = executor or E2BExecutorServiceImpl()
        self.process_runner = process_runner or ProcessRunner()
        self.git = git_manager or GitManager()

    async def sandbox_evaluate_node(self, state: CycleState) -> dict[str, Any]:
        """
        Executes the mechanical verification blockade: Linting, Type Checking, and Testing.
        All checks must pass with exit code 0 to proceed to the audit phase.
        """
        console.print("[bold cyan]Running Mechanical Verification Blockade...[/bold cyan]")

        try:
            async with workspace_lock:
                # JIT Synchronization: Ensure local workspace matches the cycle's branch
                # Prioritize cycle-specific branch (Jules PR) over base feature branch
                target_branch = state.branch_name or state.feature_branch
                if target_branch:
                    console.print(f"[dim]Synchronizing workspace to branch: {target_branch}[/dim]")
                    # Use force=True to ensure transient artifacts (.coverage, etc.) don't block sync
                    await self.git.checkout_branch(target_branch, force=True)
                    await self.git.pull_changes()

                timeout_limit = settings.sandbox.timeout

                import shlex

                # Fallback to defaults if empty
                lint_cmd = settings.sandbox.lint_check_cmd or ["uv", "run", "ruff", "check", "."]
                type_cmd = settings.sandbox.type_check_cmd or ["uv", "run", "mypy", "."]

                # `test_cmd` might be a string based on `SandboxConfig`
                raw_test_cmd = settings.sandbox.test_cmd or "uv run pytest"
                if isinstance(raw_test_cmd, str):
                    try:
                        test_cmd = shlex.split(raw_test_cmd)
                    except ValueError:
                        test_cmd = raw_test_cmd.split()
                else:
                    test_cmd = raw_test_cmd

                commands = {
                    "lint": lint_cmd,
                    "type": type_cmd,
                    "test": test_cmd,
                }
                results = {}

                for check_name, cmd in commands.items():
                    out, err, code, timeout_occurred = await self.process_runner.run_command(
                        cmd, cwd=self.git.cwd, check=False, timeout_seconds=timeout_limit
                    )
                    results[check_name] = VerificationResult(
                        command=" ".join(cmd),
                        exit_code=code,
                        stdout=out,
                        stderr=err,
                        timeout_occurred=timeout_occurred,
                    )

            # --- Lock released, process results ---
            report = StructuralGateReport(
                lint_result=results["lint"],
                type_check_result=results["type"],
                test_result=results["test"],
            )

            if not report.passed:
                console.print(
                    "[bold red]Mechanical Blockade Enforced: Structural failure detected.[/bold red]"
                )
                error_trace = report.get_failure_report()

                test_update = state.test.model_copy(update={"structural_report": report})
                committee_update = state.committee.model_copy(
                    update={"iteration_count": state.committee.iteration_count + 1}
                )
                return {
                    "status": FlowStatus.TDD_FAILED,
                    "error": f"Verification failed:\n{error_trace}",
                    "test": test_update,
                    "committee": committee_update,
                }

        except Exception as e:
            console.print(f"[bold red]Execution Error in Verification Gate: {e}[/bold red]")
            return {
                "status": FlowStatus.FAILED,
                "error": f"Sandbox error: {e!s}",
            }
        else:
            console.print("[bold green]All structural checks passed.[/bold green]")
            test_update = state.test.model_copy(update={"structural_report": report})

            return {
                "status": FlowStatus.COMPLETED,
                "test": test_update,
                "error": None,
            }
