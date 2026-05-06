import contextlib
from pathlib import Path

from src.messages import RecoveryMessages
from src.utils import logger

from .base import BaseGitManager


class GitMergingMixin(BaseGitManager):
    """Mixin for Git merging and PR operations."""

    async def _ensure_no_pending_merge(self) -> None:
        """Aborts any pending merge to ensure clean index."""
        # Check if MERGE_HEAD exists (indicates merge in progress)
        merge_head = Path.cwd() / ".git" / "MERGE_HEAD"
        if merge_head.exists():
            logger.warning("Pending merge detected. Aborting to clean index...")
            try:
                await self._run_git(["merge", "--abort"], check=False)
            except Exception as e:
                logger.warning(f"Failed to abort merge: {e}")
                # Try hard reset if abort fails? No, too dangerous.
                # Just removing the file might leave index dirty.

        # Also ensure no cherry-pick or revert in progress
        for fname in ["CHERRY_PICK_HEAD", "REVERT_HEAD"]:
            fpath = Path.cwd() / ".git" / fname
            if fpath.exists():
                logger.warning(f"Pending {fname} detected. Aborting...")
                await self._run_git(["quit"], check=False)  # 'quit' works for cherry-pick/revert

    def _validate_branch_name(self, branch_name: str) -> None:
        """Validates branch names to prevent command injection or unintended git behavior."""
        import re

        if not branch_name:
            msg = "Branch name cannot be empty"
            raise ValueError(msg)

        if len(branch_name) > 255:
            msg = f"Branch name too long: {branch_name}"
            raise ValueError(msg)

        if ".." in branch_name:
            msg = f"Branch name cannot contain path traversal sequences: {branch_name}"
            raise ValueError(msg)

        if branch_name.startswith("-"):
            msg = f"Branch name cannot start with a hyphen: {branch_name}"
            raise ValueError(msg)

        # Allow alphanumeric, -, _, /, but disallow starting with -, containing spaces, or control characters.
        if not re.match(r"^[a-zA-Z0-9_][a-zA-Z0-9_/-]*$", branch_name):
            msg = f"Invalid branch name format: {branch_name}"
            raise ValueError(msg)

    async def safe_merge_with_conflicts(self, branch_name: str) -> bool:
        """
        Attempts to merge the given branch into the current branch without committing.
        Returns True if successful, False if conflicts exist.
        Crucially, it leaves the working directory dirty with Git conflict markers if conflicts occur.
        """
        self._validate_branch_name(branch_name)
        logger.info(f"Safely merging {branch_name} into current branch...")

        try:
            _stdout, _stderr, code, _ = await self.runner.run_command(
                ["git", "merge", "--no-commit", "--no-ff", branch_name],
                check=False,
            )

        except Exception as e:
            logger.error(f"Error during safe_merge_with_conflicts: {e}")
            try:
                await self._run_git(["merge", "--abort"], check=False)
            except Exception as abort_err:
                logger.error(f"Failed to abort safe merge: {abort_err}")

            msg = f"Failed to merge safely: {e}"
            raise RuntimeError(msg) from e
        else:
            if code == 0:
                logger.info(f"Successfully merged {branch_name} without conflicts.")
                return True

            logger.warning(
                f"Conflicts detected when merging {branch_name}. Leaving markers intact."
            )
            return False

    async def merge_branch(self, target: str, source: str) -> None:
        """Merges source into target."""
        self._validate_branch_name(target)
        self._validate_branch_name(source)

        logger.info(f"Merging {source} into {target}...")
        original_branch = await self.get_current_branch()  # type: ignore

        await self._run_git(["checkout", target])

        try:
            await self._run_git(["merge", source])
        except RuntimeError as e:
            logger.error(f"Merge conflict detected: {e}")
            try:
                await self._run_git(["merge", "--abort"], check=False)
            except Exception as abort_err:
                logger.error(f"Failed to abort merge after conflict: {abort_err}")

            with contextlib.suppress(Exception):
                await self._run_git(["checkout", original_branch])

            error_msg = RecoveryMessages.merge_conflict(source, target, original_branch)
            raise RuntimeError(error_msg) from e
        except Exception as e:
            logger.error(f"Unexpected error during merge: {e}")
            try:
                await self._run_git(["merge", "--abort"], check=False)
            except Exception as abort_err:
                logger.error(f"Failed to abort merge after unexpected error: {abort_err}")
            raise

    async def merge_pr(self, pr_number: int | str, method: str = "squash") -> None:
        """
        Merge PR using gh CLI with auto-merge capability.
        Automatically converts Draft PRs to Ready before merging.
        """
        pr = str(pr_number)

        # 0. Ensure no pending merge conflict state exists
        await self._ensure_no_pending_merge()

        # 1. Check if Draft and mark ready if needed
        try:
            stdout, stderr, code, _ = await self.runner.run_command(
                [self.gh_cmd, "pr", "view", pr, "--json", "isDraft", "--jq", ".isDraft"],
                check=False,
            )
            if code == 0 and stdout.strip() == "true":
                logger.info(f"PR {pr} is a draft. Marking as ready for review...")
                await self.runner.run_command([self.gh_cmd, "pr", "ready", pr], check=True)
        except Exception as e:
            logger.warning(f"Failed to check/update PR draft status: {e}")

        # 2. Merge
        logger.info(f"Merging PR {pr} using method={method}")

        cmd_immediate = [
            self.gh_cmd,
            "pr",
            "merge",
            pr,
            f"--{method}",
            "--delete-branch",
        ]

        stdout, stderr, code, _ = await self.runner.run_command(cmd_immediate, check=False)

        if code == 0:
            logger.info(f"Successfully merged PR {pr} immediately")
            return

        fallback_keywords = [
            "status check",
            "review",
            "protected",
            "requirement",
            "blocking",
            "wait",
        ]

        if any(keyword in stderr.lower() for keyword in fallback_keywords):
            logger.info(f"Immediate merge failed ({stderr.strip()}). Attempting auto-merge...")
            cmd_auto = [self.gh_cmd, "pr", "merge", pr, f"--{method}", "--auto", "--delete-branch"]
            _, stderr_auto, code_auto, _ = await self.runner.run_command(cmd_auto, check=True)

            if code_auto == 0:
                logger.info(f"Successfully enabled auto-merge for PR {pr}")
                return

            msg = f"Failed to auto-merge PR {pr}: {stderr_auto}"
            raise RuntimeError(msg)

        msg = f"Failed to merge PR {pr}: {stderr}"
        raise RuntimeError(msg)

    async def create_final_pr(self, integration_branch: str, title: str, body: str) -> str:
        """Creates final PR from integration branch to main."""
        logger.info(f"Creating final PR: {integration_branch} → main")

        stdout, _stderr, code, _ = await self.runner.run_command(
            [
                self.gh_cmd,
                "pr",
                "list",
                "--head",
                integration_branch,
                "--base",
                "main",
                "--json",
                "url",
                "--jq",
                ".[0].url",
            ],
            check=False,
        )

        if code == 0 and stdout.strip():
            existing_pr_url = str(stdout.strip())
            logger.info(f"PR already exists: {existing_pr_url}")
            return existing_pr_url

        await self._run_git(["checkout", integration_branch])

        try:
            await self._run_git(["pull"])
        except RuntimeError as e:
            logger.warning(f"Pull failed before push (proceeding anyway): {e}")

        await self.push_branch(integration_branch)  # type: ignore[attr-defined]

        stdout, _stderr, code, _ = await self.runner.run_command(
            [
                self.gh_cmd,
                "pr",
                "create",
                "--base",
                "main",
                "--head",
                integration_branch,
                "--title",
                title,
                "--body",
                body,
            ],
            check=True,
        )

        if code != 0:
            errmsg = f"Failed to create PR: {stdout or 'Unknown error'}"
            raise RuntimeError(errmsg)

        pr_url = str(stdout.strip())
        logger.info(f"Final PR created: {pr_url}")
        return pr_url
