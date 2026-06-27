import re
import shlex
from typing import Any

from src.config import settings
from src.domain_models import UatExecutionState
from src.domain_models.multimodal_artifact_schema import MultiModalArtifact
from src.enums import FlowStatus
from src.process_runner import ProcessRunner
from src.services.git_ops import GitManager
from src.state import CycleState
from src.utils import logger, redact_secrets


class UatUseCase:
    """
    Encapsulates the logic for UAT Evaluation, Auto-Merge, and Refactoring Transition.
    """

    TEST_ID_PATTERN: re.Pattern[str] = re.compile(r"^[\w\.-]+$")
    PR_URL_PATTERN: re.Pattern[str] = re.compile(
        r"^https://github\.com/[\w.-]+/[\w.-]+/pull/\d+/?$"
    )

    def __init__(self, git_manager: GitManager) -> None:
        """
        Initializes the UatUseCase.

        Args:
            git_manager (GitManager): Instance for executing git operations like PR merges.
        """
        if not git_manager:
            msg = "GitManager must be injected into UatUseCase"
            raise ValueError(msg)
        self.git = git_manager

    def _scan_artifacts(self, stdout: str, stderr: str) -> list[MultiModalArtifact]:
        """
        Scans the local artifacts directory for multi-modal test artifacts (screenshots/traces).

        Args:
            stdout (str): Raw stdout from the test runner.
            stderr (str): Raw stderr from the test runner.

        Returns:
            list[MultiModalArtifact]: Validated list of multimodal artifacts.
        """
        artifacts_dir = settings.paths.artifacts_dir

        if not artifacts_dir.exists() or not artifacts_dir.is_dir():
            return []

        try:
            artifacts_dir = artifacts_dir.resolve(strict=True)
        except Exception as e:
            logger.error(f"Failed to resolve artifacts directory path: {e}")
            return []

        artifacts = []

        # Scan for multi-modal artifacts if directory exists
        if artifacts_dir.exists() and artifacts_dir.is_dir():
            # We expect PNG screenshots and ZIP traces named like {test_id}.png / {test_id}_trace.zip
            for img_path in artifacts_dir.glob("*.png"):
                # Validate full path to prevent directory traversal
                try:
                    resolved_img = img_path.resolve(strict=True)
                    if not resolved_img.is_relative_to(artifacts_dir):
                        logger.warning(f"Artifact path traversal detected: {img_path}")
                        continue
                except Exception as e:
                    logger.warning(f"Failed to resolve artifact path {img_path}: {e}")
                    continue

                base_name = img_path.stem
                # Validate test_id to prevent path traversal
                if not self.TEST_ID_PATTERN.match(base_name):
                    logger.warning(f"Invalid artifact filename: {base_name}")
                    continue

                zip_path = artifacts_dir / f"{base_name}_trace.zip"
                dom_path = artifacts_dir / f"{base_name}_dom.txt"

                if img_path.exists():
                    try:
                        artifact = MultiModalArtifact(
                            test_id=base_name,
                            screenshot_path=str(img_path),
                            trace_path=str(zip_path) if zip_path.exists() else None,
                            dom_snapshot_path=str(dom_path) if dom_path.exists() else None,
                            console_logs=[],
                            traceback=(
                                stderr[-settings.uat.traceback_limit :]
                                if stderr
                                else stdout[-settings.uat.traceback_limit :]
                            ),
                        )
                        artifacts.append(artifact)
                    except Exception as e:
                        logger.warning(f"Failed to parse artifact {base_name}: {e}")
        return artifacts

    async def execute(self, state: CycleState) -> dict[str, Any]:
        """
        Executes the UAT Evaluation node.

        It deterministically executes Pytest with Playwright, evaluates the exit code,
        collects mult-modal artifacts on failure, or delegates to success handling.
        """
        logger.info("Running UAT Evaluation...")

        # Dynamic Execution using ProcessRunner for local process execution
        runner = ProcessRunner()

        # Ensure we run the exact UAT tests folder with configurable browser args
        base_cmd = shlex.split(settings.uat.test_cmd)
        cmd = [*base_cmd, *settings.uat.playwright_args]

        # Ensure a clean state before executing dynamic UAT
        if settings.uat.db_reset_cmd:
            logger.debug("Resetting database state...")
            cmd_str = settings.uat.db_reset_cmd
            if cmd_str:
                reset_cmd = shlex.split(cmd_str)
                await runner.run_command(reset_cmd, check=True)

        logger.debug(f"Executing: {redact_secrets(' '.join(cmd))}")
        stdout, stderr, exit_code, _timeout = await runner.run_command(cmd, check=False)

        if exit_code != 0:
            logger.error(f"UAT Execution Failed with exit code {exit_code}.")
            artifacts = self._scan_artifacts(stdout, stderr)

            uat_state = UatExecutionState(
                exit_code=exit_code, stdout=stdout, stderr=stderr, artifacts=artifacts
            )
            uat_update = state.uat.model_copy(update={"uat_execution_state": uat_state})
            return {
                "status": FlowStatus.UAT_FAILED,
                "uat": uat_update,
                "error": "UAT dynamically failed",
            }

        logger.info("UAT Execution Passed.")
        return await self._handle_success(state)

    async def _handle_success(self, state: CycleState) -> dict[str, Any]:
        """
        Handles the logic when UAT passes.
        """
        logger.info("UAT Execution successfully verified.")
        return {"status": FlowStatus.COMPLETED}
