from pathlib import Path

from src.config import settings
from src.process_runner import ProcessRunner
from src.utils import logger
from src.utils_sanitization import redact_secrets


class BaseGitManager:
    """Base class for Git operations."""

    def __init__(self, cwd: Path | None = None) -> None:
        self.runner = ProcessRunner()
        self.git_cmd = "git"
        self.gh_cmd = settings.tools.gh_cmd
        self.cwd = cwd

    async def _ensure_no_lock(self) -> None:
        """Removes stale index.lock file if it exists."""
        lock_file = Path.cwd() / ".git" / "index.lock"
        if lock_file.exists():
            try:
                # We assume single-threaded git access in this agent context.
                lock_file.unlink()
                logger.warning("Removed stale .git/index.lock file")
            except OSError as e:
                logger.warning(f"Could not remove .git/index.lock: {e}")

    async def _run_git(self, args: list[str], check: bool = True) -> str:
        # Check for lock before running any command
        import asyncio
        import logging
        import random

        logger = logging.getLogger(__name__)
        for attempt in range(5):
            await self._ensure_no_lock()
            cmd = [self.git_cmd, *args]
            stdout, stderr, code, _ = await self.runner.run_command(cmd, cwd=self.cwd, check=False)

            error_msg = str(stderr).strip() or str(stdout).strip()

            if code == 0:
                return str(stdout).strip()

            if "index.lock" in error_msg and attempt < 4:
                logger.warning(f"Index locked, retrying {redact_secrets(' '.join(args))}...")
                await asyncio.sleep(random.SystemRandom().uniform(0.5, 2.0))
                continue

            if (
                args
                and args[0] == "pull"
                and (
                    "no tracking information" in error_msg or "could not read Username" in error_msg
                )
            ):
                logger.warning(f"Git pull tracking/auth error suppressed: {error_msg}")
                return ""

            if code != 0 and check:
                msg = f"Git command failed: {redact_secrets(' '.join(cmd))} - Stderr: {error_msg}"
                raise RuntimeError(msg)
            return str(stdout).strip()
        return ""

    async def get_current_commit(self) -> str:
        """Returns the current commit hash (HEAD)."""
        stdout, _stderr, _code, _ = await self.runner.run_command(
            [self.git_cmd, "rev-parse", "HEAD"], cwd=self.cwd, check=True
        )
        return str(stdout).strip()

    async def get_status(self) -> str:
        return await self._run_git(["status", "--porcelain"], check=False)

    async def add_all(self) -> None:
        """Stages all changes in the current directory."""
        # Simplified staging to avoid 'add ignored' errors.
        # We rely on .gitignore and EnvironmentValidator to keep ephemeral files out.
        await self._run_git(["add", "."])

    async def commit(self, message: str) -> None:
        await self._run_git(["commit", "-m", message])

    async def fetch_changes(self) -> None:
        """Fetch latest changes from origin."""
        await self._run_git(["fetch", "origin"], check=False)

    async def reset_hard(self) -> None:
        await self._run_git(["reset", "--hard", "HEAD"])
        await self._run_git(["clean", "-fd"])

    async def _auto_commit_if_dirty(self, message: str = "Auto-save") -> None:
        """Automatically commits changes if the working directory is dirty."""
        # Check for uncommitted changes
        stdout, _stderr, _code, _ = await self.runner.run_command(
            [self.git_cmd, "status", "--porcelain"], check=False
        )
        if stdout.strip():
            # CRITICAL: Check for unmerged files (conflicts) before committing
            # Codes: DD, AU, UD, UA, DU, AA, UU
            lines = stdout.splitlines()
            from src.config import settings

            conflict_codes = settings.tools.conflict_codes
            has_conflicts = any(line[:2] in conflict_codes for line in lines)

            if has_conflicts:
                logger.warning(
                    "Unresolved conflicts detected! Attempting to restore clean state..."
                )
                # Attempt to abort common operations that leave unmerged files
                for abort_cmd in [
                    ["rebase", "--abort"],
                    ["merge", "--abort"],
                    ["cherry-pick", "--abort"],
                    ["am", "--abort"],
                ]:
                    try:
                        await self._run_git(abort_cmd)
                        logger.info(f"✓ Executed git {abort_cmd[0]} --abort")
                    except Exception:
                        logger.debug(f"Command {abort_cmd[0]} --abort failed (likely not running)")
                        # Ignore failures for commands not currently running

                # Re-check status after abort attempts
                stdout, _, _, _ = await self.runner.run_command(
                    [self.git_cmd, "status", "--porcelain"], check=False
                )
                if any(line[:2] in conflict_codes for line in stdout.splitlines()):
                    error_msg = (
                        "Could not automatically resolve git conflicts. "
                        "Manual intervention may be required."
                    )
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)

                # If conflicts are gone but changes remain, we can proceed with auto-commit
                if not stdout.strip():
                    return

            logger.info("Uncommitted changes detected. Auto-committing...")
            await self.add_all()
            # No longer need to manually unstage here because add_all() now excludes them.
            # But we still check if there's anything staged to commit.
            remaining, _, _, _ = await self.runner.run_command(
                [self.git_cmd, "status", "--porcelain", "--untracked-files=no"], check=False
            )
            if not remaining.strip():
                logger.info("Nothing to commit after selective staging.")
                return
            await self._run_git(["commit", "-m", message])
            logger.info("✓ Auto-committed changes.")
