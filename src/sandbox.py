import io
import shlex
from pathlib import Path

from e2b_code_interpreter import Sandbox

from .config import settings
from .services.sandbox.sync import SandboxSyncManager
from .utils import logger
from .utils_sanitization import redact_secrets


class SandboxRunner:
    """
    Executes code and commands in an E2B Sandbox for safety and isolation.
    """

    def __init__(self, sandbox_id: str | None = None, cwd: str | None = None) -> None:
        self.api_key = settings.E2B_API_KEY.get_secret_value()
        if self.api_key is None:
            msg = "E2B_API_KEY environment variable is not set"
            raise ValueError(msg)

        cwd_to_use = cwd or settings.sandbox.cwd
        if not cwd_to_use:
            msg = "Working directory must be specified"
            raise ValueError(msg)

        # Sanitize and validate path strictly
        resolved_cwd = str(Path(cwd_to_use).resolve())

        allowed_prefixes = settings.sandbox.allowed_cwd_prefixes
        if not any(resolved_cwd.startswith(prefix) for prefix in allowed_prefixes):
            msg = f"Invalid sandbox working directory: {resolved_cwd}. Must start with one of {allowed_prefixes}"
            raise ValueError(msg)

        # .resolve() handles traversal, but if there's any anomaly:
        if ".." in resolved_cwd:
            msg = "Directory traversal not allowed in sandbox working directory"
            raise ValueError(msg)

        self.cwd = resolved_cwd

        self.sandbox_id = sandbox_id
        self.sandbox: Sandbox | None = None
        self._last_sync_hash: str | None = None

    async def get_sandbox(self) -> Sandbox:
        """Get or create a sandbox instance."""
        if self.sandbox:
            return self.sandbox

        if self.sandbox_id:
            try:
                logger.info(f"Connecting to existing sandbox: {self.sandbox_id}")
                self.sandbox = Sandbox.connect(self.sandbox_id, api_key=self.api_key)
            except Exception as e:
                logger.warning(
                    f"Failed to connect to sandbox {self.sandbox_id}: {e}. Creating new."
                )
            else:
                return self.sandbox

        await self._create_new_sandbox()
        return self.sandbox

    async def _create_new_sandbox(self) -> None:
        """Creates and initializes a new sandbox instance."""
        logger.info("Creating new E2B Sandbox...")
        import asyncio

        max_retries = settings.sandbox.max_retries
        for attempt in range(max_retries + 1):
            try:
                self.sandbox = Sandbox.create(
                    api_key=self.api_key,
                    template=settings.sandbox.template,
                    timeout=settings.sandbox.timeout,
                )
                break
            except Exception as e:
                if attempt < max_retries:
                    logger.warning(f"Failed to create sandbox: {e}. Retrying...")
                    await asyncio.sleep(2**attempt)
                else:
                    logger.error(f"Sandbox creation failed after {max_retries} retries.")
                    msg = f"Failed to create E2B Sandbox: {e}"
                    raise RuntimeError(msg) from e

        if self.sandbox:
            self.sandbox.commands.run(["mkdir", "-p", self.cwd], timeout=settings.sandbox.timeout)
            await self._sync_to_sandbox(self.sandbox)

            if settings.sandbox.install_cmd:
                try:
                    # Parse and pass as list to prevent shell injection via string eval
                    parsed_cmd = shlex.split(settings.sandbox.install_cmd)
                except ValueError as e:
                    msg = f"Invalid install_cmd configuration: {e}"
                    raise ValueError(msg) from e

                self.sandbox.commands.run(parsed_cmd, timeout=settings.sandbox.timeout)

            self.sandbox.commands.run(["mkdir", "-p", self.cwd], timeout=settings.sandbox.timeout)
            await self._sync_to_sandbox(self.sandbox)

    def _validate_command(self, cmd: list[str]) -> None:
        if not cmd:
            msg = "Command cannot be empty"
            raise ValueError(msg)

        allowed = False
        base_cmd = cmd[0]
        # Command whitelist
        whitelist = [
            "pytest",
            "uv",
            "ruff",
            "mypy",
            "git",
            "python",
            "python3",
            "ls",
            "cat",
            "echo",
            "pwd",
            "pip",
        ]

        if base_cmd in whitelist:
            allowed = True

        if not allowed:
            msg = f"Command '{base_cmd}' is not in the allowed whitelist."
            raise ValueError(msg)

        import re

        forbidden_chars = re.compile(r"[&|<>;$`\\]")
        for arg in cmd:
            if forbidden_chars.search(arg):
                msg = (
                    f"Command argument contains forbidden characters (shell injection risk): {arg}"
                )
                raise ValueError(msg)

    async def run_command(
        self, cmd: list[str], check: bool = False, env: dict[str, str] | None = None
    ) -> tuple[str, str, int]:
        """
        Runs a shell command in the sandbox with retry logic.
        Enforces a strict command whitelist to prevent execution of arbitrary, unsafe commands.
        """
        self._validate_command(cmd)

        redacted_cmd = redact_secrets(" ".join(cmd))
        logger.info(f"Sandbox executing explicit command structure: {redacted_cmd}")

        max_retries = settings.sandbox.max_retries
        stdout = ""
        stderr = ""
        exit_code = 0

        import asyncio

        for attempt in range(max_retries + 1):
            try:
                sandbox = await self.get_sandbox()
                await self._sync_to_sandbox(sandbox)

                logger.info(f"[Sandbox] Running (Attempt {attempt + 1}): {redacted_cmd}")

                # Build sandbox environment: start from caller-supplied env vars,
                # then explicitly clear Docker-host-specific variables that must not
                # leak into the E2B sandbox.
                # UV_PROJECT_ENVIRONMENT is set to /opt/ac_cdd_project_venv in the
                # Docker container (to avoid host-venv path leakage), but /opt/ is
                # not writable inside E2B, so ruff/mypy fail with "Permission denied".
                sandbox_env: dict[str, str] = dict(env or {})
                # Forcefully clear the variables inherited from the Docker container
                for env_var in settings.sandbox.sandbox_env_cleanup:
                    if env_var in sandbox_env:
                        sandbox_env.pop(env_var)

                exec_result = sandbox.commands.run(
                    cmd, cwd=self.cwd, envs=sandbox_env, timeout=settings.sandbox.timeout
                )
                stdout = exec_result.stdout
                stderr = exec_result.stderr
                exit_code = exec_result.exit_code or 0
                break

            except Exception as e:
                err_msg = str(e).lower()
                is_sandbox_error = (
                    "sandbox was not found" in err_msg
                    or "timeout" in err_msg
                    or "sandbox error" in err_msg
                )

                if is_sandbox_error and attempt < max_retries:
                    logger.warning(
                        f"Sandbox disconnection detected: {e}. Re-initializing sandbox..."
                    )
                    if self.sandbox:
                        try:
                            self.sandbox.kill()
                        except Exception as sandbox_kill_err:
                            logger.debug(f"Failed to kill sandbox: {sandbox_kill_err}")
                        self.sandbox = None
                        self._last_sync_hash = None
                    await asyncio.sleep(2**attempt)
                    continue

                if hasattr(e, "exit_code") and hasattr(e, "stdout") and hasattr(e, "stderr"):
                    stdout = e.stdout
                    stderr = e.stderr
                    exit_code = e.exit_code
                    break
                raise

        if check and exit_code != 0:
            msg = f"Command failed with code {exit_code}:\nSTDOUT: {stdout}\nSTDERR: {stderr}"
            raise RuntimeError(msg)

        return stdout, stderr, exit_code

    def _compute_sync_hash(self) -> str:
        """Computes hash of directories to sync."""
        return SandboxSyncManager.compute_sync_hash()

    def _create_sync_tarball(self) -> io.BytesIO:
        """Creates a tarball of files to sync."""
        return SandboxSyncManager.create_sync_tarball()

    async def _sync_to_sandbox(self, sandbox: Sandbox | None = None) -> None:
        """
        Uploads configured directories and files to the sandbox using a tarball for performance.
        Skips if content hasn't changed.
        """
        if sandbox is None:
            sandbox = self.sandbox
            if sandbox is None:
                return

        current_hash = self._compute_sync_hash()

        if self._last_sync_hash == current_hash:
            logger.info("Sandbox files up-to-date. Skipping sync.")
            return

        tar_buffer = self._create_sync_tarball()

        remote_tar_path = f"{self.cwd}/bundle.tar.gz"
        if not remote_tar_path.startswith(self.cwd):
            msg = "Invalid remote tar path destination"
            raise ValueError(msg)

        sandbox.files.write(remote_path=remote_tar_path, data=tar_buffer)

        sandbox.commands.run(
            ["tar", "-xzf", remote_tar_path, "-C", self.cwd],
            timeout=settings.sandbox.timeout,
        )
        logger.info("Synced files to sandbox via tarball.")
        self._last_sync_hash = current_hash

    async def cleanup(self) -> None:
        """alias for close, matching test expectations"""
        await self.close()

    async def close(self) -> None:
        if self.sandbox:
            self.sandbox.kill()
            self.sandbox = None
