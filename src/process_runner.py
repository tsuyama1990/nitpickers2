import asyncio
import subprocess
from pathlib import Path

from .utils import logger, redact_secrets


class ProcessRunner:
    """
    Handles asynchronous process execution with logging and output capture.
    """

    async def run_command(
        self,
        cmd: list[str],
        cwd: Path | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
        timeout_seconds: int | float | None = None,
    ) -> tuple[str, str, int, bool]:
        """
        Executes a command asynchronously.
        Returns: (stdout, stderr, exit_code, timeout_occurred)
        """
        cmd_str = " ".join(cmd)
        redacted_cmd_str = redact_secrets(cmd_str)
        logger.debug(f"Running command: {redacted_cmd_str}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout_seconds
                )
            except TimeoutError:
                process.kill()
                # Wait for it to actually terminate
                await process.communicate()
                return "", f"Command timed out after {timeout_seconds} seconds", -1, True

            stdout_str = stdout.decode().strip() if stdout else ""
            stderr_str = stderr.decode().strip() if stderr else ""
            returncode = process.returncode or 0

            if returncode != 0:
                if check:
                    logger.error(f"Command failed [{returncode}]: {redacted_cmd_str}")
                    if stderr_str:
                        logger.error(f"Stderr: {stderr_str}")

                    raise subprocess.CalledProcessError(  # noqa: TRY301
                        returncode, cmd, output=stdout_str, stderr=stderr_str
                    )
                logger.debug(f"Command failed (expected) [{returncode}]: {redacted_cmd_str}")
        except Exception as e:
            if check and isinstance(e, subprocess.CalledProcessError):
                raise
            logger.error(f"Execution failed for '{cmd_str}': {e}")
            return "", str(e), -1, False
        else:
            return stdout_str, stderr_str, returncode, False
