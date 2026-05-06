import contextlib
import os

from src.utils import logger

from .base import BaseGitManager


class GitCheckoutMixin(BaseGitManager):
    """Mixin for Git checkout and stash operations."""

    async def _checkout_pr(self, target: str, force: bool) -> None:
        """Helper to checkout a PR using gh CLI."""
        try:
            stdout, _, _, _ = await self.runner.run_command(
                [
                    self.gh_cmd,
                    "pr",
                    "view",
                    target,
                    "--json",
                    "headRefName",
                    "-q",
                    ".headRefName",
                ],
                check=False,
            )
            head_branch = str(stdout).strip()
            current = await self.get_current_branch()
            if head_branch and current == head_branch:
                logger.debug(f"Already on PR branch {head_branch}, skipping checkout.")
                return
        except Exception:
            logger.debug("Failed to pre-check PR branch name, proceeding with checkout.")

        cmd = [self.gh_cmd, "pr", "checkout", target]
        if force:
            cmd.append("--force")
        await self.runner.run_command(cmd, check=True)

    async def _checkout_branch(self, target: str, force: bool) -> None:
        """Helper to checkout a regular branch."""
        cmd = ["checkout", target]
        if force:
            cmd.append("-f")
        try:
            await self._run_git(cmd)
        except Exception as e:
            if "already used by worktree" in str(e):
                logger.warning(
                    f"Branch {target} is locked by another worktree. Falling back to detached checkout."
                )
                await self._run_git(["checkout", "--detach", target])
            else:
                raise

        # IMPORTANT: Always try to sync with remote to get any freshly merged PRs
        with contextlib.suppress(Exception):
            await self._run_git(["fetch"])
            await self.pull_changes()

    async def smart_checkout(self, target: str, is_pr: bool = False, force: bool = False) -> None:
        """Robust checkout that handles local changes by auto-committing."""
        await self._auto_commit_if_dirty()

        # Optimization: Skip if already on the target branch
        if not is_pr:
            current = await self.get_current_branch()
            if current == target:
                logger.debug(f"Already on branch {target}, skipping checkout.")
                return

        try:
            if is_pr:
                await self._checkout_pr(target, force)
            else:
                await self._checkout_branch(target, force)
        except Exception:
            logger.error(f"Failed to checkout '{target}'. Please check git status.")
            raise

    async def checkout_pr(self, pr_url: str) -> None:
        """Checks out the Pull Request branch using GitHub CLI."""
        logger.info(f"Checking out PR: {pr_url}...")
        # Use force=True to overwrite any local divergence (e.g. from auto-commits)
        await self.smart_checkout(pr_url, is_pr=True, force=True)

        logger.info("Pulling latest commits from PR...")
        try:
            await self._run_git(["pull"])
        except Exception as e:
            logger.warning(f"Could not pull latest commits: {e}")
        logger.info(f"Checked out PR {pr_url} successfully.")

    async def get_pr_base_branch(self, pr_url: str) -> str:
        """
        Gets the base branch name for a given PR URL.
        Useful for determining the correct diff target.
        """
        from src.config import settings

        default_branch = settings.DEFAULT_BASE_BRANCH
        try:
            # gh pr view <url> --json baseRefName -q .baseRefName
            stdout, _stderr, _code, _ = await self.runner.run_command(
                [self.gh_cmd, "pr", "view", pr_url, "--json", "baseRefName", "-q", ".baseRefName"],
                check=True,
            )
            base_branch = str(stdout).strip()
            if base_branch:
                return base_branch
            logger.warning(
                f"Could not determine base branch for PR {pr_url}, defaulting to {default_branch}"
            )
            return default_branch  # noqa: TRY300
        except Exception as e:
            logger.warning(f"Failed to get PR base branch: {e}")
            return default_branch

    async def checkout_branch(self, branch_name: str, force: bool = False) -> None:
        """Checks out an existing branch."""
        with contextlib.suppress(Exception):
            await self._run_git(["fetch"])

        logger.info(f"Checking out branch: {branch_name}...")
        await self.smart_checkout(branch_name, is_pr=False, force=force)

    async def ensure_clean_state(self, force_stash: bool = False) -> None:
        """Ensures the working directory is clean."""
        await self._auto_commit_if_dirty("Auto-save before workflow run")

    async def commit_changes(self, message: str) -> bool:
        """Stages and commits all changes."""
        await self.add_all()
        status = await self._run_git(["status", "--porcelain", "--untracked-files=no"])
        if not status:
            return False
        await self._run_git(["commit", "-m", message])
        return True

    async def pull_changes(self) -> None:
        """Pulls changes from the remote repository using rebase."""
        await self.ensure_clean_state()
        logger.info("Pulling latest changes (rebase)...")
        try:
            # Check for current branch name
            branch = await self.get_current_branch()

            # Ensure tracking is set (fixes "no tracking information" warning)
            if branch:
                with contextlib.suppress(Exception):
                    await self._run_git(["branch", f"--set-upstream-to=origin/{branch}", branch])

            await self._run_git(["pull", "--rebase"])
            logger.info("Changes pulled successfully.")
        except Exception as e:
            logger.warning(f"pull --rebase failed ({e}). Aborting rebase to restore clean state.")
            # Abort the rebase so the repo doesn't get stuck in a mid-rebase state
            try:
                await self._run_git(["rebase", "--abort"])
                logger.info("Rebase aborted successfully.")
            except Exception as abort_err:
                logger.warning(f"Could not abort rebase: {abort_err}")

            # If we are in an isolated worktree (self.cwd is set), discard conflicting local auto-commits
            # and reset hard to match the PR state exactly.
            if self.cwd:
                logger.warning(
                    f"Fallback: Hard resetting to origin/{branch} in worktree to resolve conflict."
                )
                await self._run_git(["fetch", "origin", branch])
                await self._run_git(["reset", "--hard", f"origin/{branch}"])
            else:
                raise

    async def get_current_branch(self) -> str:
        """Returns the name of the currently checked out branch."""
        stdout, _, _, _ = await self.runner.run_command(
            [self.git_cmd, "branch", "--show-current"], check=False
        )
        return str(stdout).strip()

    async def push_branch(self, branch: str, force: bool = False) -> None:
        """Pushes the specified branch to origin, skipping if already pushed in this batch."""
        from src.services.git_ops import _pushed_commit_hashes

        current_hash = await self.get_current_commit()
        if _pushed_commit_hashes.get(branch) == current_hash and not force:
            logger.debug(
                f"Skipping redundant push for branch {branch} (hash {current_hash} already pushed)"
            )
            return

        if os.environ.get("GITHUB_TOKEN"):
            with contextlib.suppress(Exception):
                await self.runner.run_command([self.gh_cmd, "auth", "setup-git"], check=False)

        logger.info(f"Pushing branch {branch} to origin (force={force})...")
        cmd = ["push", "-u", "origin", branch]
        if force:
            cmd.append("--force")
        await self._run_git(cmd)
        _pushed_commit_hashes[branch] = current_hash

    async def get_diff(self, target_branch: str | None = None) -> str:
        """Returns the diff between HEAD and target branch."""
        from src.config import settings

        target_branch = target_branch or settings.DEFAULT_BASE_BRANCH
        return await self._run_git(["diff", f"{target_branch}...HEAD"])

    async def get_changed_files(self, base_branch: str | None = None) -> list[str]:
        """Returns a list of unique file paths that have changed."""
        from src.config import settings

        base_branch = base_branch or settings.DEFAULT_BASE_BRANCH

        files = set()
        with contextlib.suppress(Exception):
            out = await self._run_git(["diff", "--name-only", f"{base_branch}...HEAD"], check=False)
            if out:
                files.update(out.splitlines())

        out = await self._run_git(["diff", "--name-only", "--cached"], check=False)
        if out:
            files.update(out.splitlines())

        out = await self._run_git(["diff", "--name-only"], check=False)
        if out:
            files.update(out.splitlines())

        out = await self._run_git(["ls-files", "--others", "--exclude-standard"], check=False)
        if out:
            files.update(out.splitlines())

        return sorted(files)
