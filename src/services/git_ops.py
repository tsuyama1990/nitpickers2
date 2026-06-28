"""Consolidated Git operations module.

Previously split across 6 files in src/services/git/ (base.py, branching.py,
checkout.py, merging.py, state.py, worktree.py), all contents are now merged
into this single file for simplicity.

Classes:
    GitManager:         All git operations (branching, checkout, merge, state, etc.)
    GitWorktreeManager: Ephemeral worktree management for parallel execution.
"""

import asyncio
import contextlib
import logging
import os
import random
import re
import shutil
import tempfile
from pathlib import Path

from src.config import settings
from src.messages import RecoveryMessages
from src.process_runner import ProcessRunner
from src.utils import logger, redact_secrets

# Global lock to synchronize parallel access to the local Git repository
workspace_lock = asyncio.Lock()

# Global state to track last pushed commit hashes per branch to avoid redundant pushes in parallel batches
_pushed_commit_hashes: dict[str, str] = {}


# ==============================================================================
# GitManager — all Git operations in one class
# ==============================================================================


class GitManager:
    """
    Manages Git operations for the AC-CDD workflow.
    All methods from the previous mixin hierarchy are defined directly here.
    """

    STATE_BRANCH = "ac-cdd/state"

    def __init__(self, cwd: Path | None = None) -> None:
        self.runner = ProcessRunner()
        self.git_cmd = "git"
        self.gh_cmd = settings.tools.gh_cmd
        self.cwd = cwd

    # ------------------------------------------------------------------
    # Core git execution  (originally base.py)
    # ------------------------------------------------------------------

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
        _logger = logging.getLogger(__name__)
        for attempt in range(5):
            await self._ensure_no_lock()
            cmd = [self.git_cmd, *args]
            stdout, stderr, code, _ = await self.runner.run_command(cmd, cwd=self.cwd, check=False)

            error_msg = str(stderr).strip() or str(stdout).strip()

            if code == 0:
                return str(stdout).strip()

            if "index.lock" in error_msg and attempt < 4:
                _logger.warning(f"Index locked, retrying {redact_secrets(' '.join(args))}...")
                await asyncio.sleep(random.SystemRandom().uniform(0.5, 2.0))
                continue

            if (
                args
                and args[0] == "pull"
                and (
                    "no tracking information" in error_msg or "could not read Username" in error_msg
                )
            ):
                _logger.warning(f"Git pull tracking/auth error suppressed: {error_msg}")
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
        stdout, _stderr, _code, _ = await self.runner.run_command(
            [self.git_cmd, "status", "--porcelain"], check=False
        )
        if stdout.strip():
            # CRITICAL: Check for unmerged files (conflicts) before committing
            lines = stdout.splitlines()
            conflict_codes = settings.tools.conflict_codes
            has_conflicts = any(line[:2] in conflict_codes for line in lines)

            if has_conflicts:
                logger.warning(
                    "Unresolved conflicts detected! Attempting to restore clean state..."
                )
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

                if not stdout.strip():
                    return

            logger.info("Uncommitted changes detected. Auto-committing...")
            await self.add_all()
            remaining, _, _, _ = await self.runner.run_command(
                [self.git_cmd, "status", "--porcelain", "--untracked-files=no"], check=False
            )
            if not remaining.strip():
                logger.info("Nothing to commit after selective staging.")
                return
            await self._run_git(["commit", "-m", message])
            logger.info("✓ Auto-committed changes.")

    # ------------------------------------------------------------------
    # Branching  (originally branching.py)
    # ------------------------------------------------------------------

    async def get_current_branch(self) -> str:
        try:
            return await self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        except RuntimeError:
            return "main"

    async def get_remote_url(self) -> str:
        """Returns the URL of the 'origin' remote."""
        return await self._run_git(["config", "--get", "remote.origin.url"])

    async def create_integration_branch(
        self, session_id: str, prefix: str = "dev", branch_name: str | None = None
    ) -> str:
        """Creates integration branch from main for the session."""
        integration_branch = branch_name if branch_name else f"{prefix}/{session_id}/integration"
        logger.info(f"Creating integration branch: {integration_branch}")

        await self._auto_commit_if_dirty()

        await self._run_git(["checkout", "main"])
        await self._run_git(["pull"])

        _stdout, _stderr, code, _ = await self.runner.run_command(
            [self.git_cmd, "rev-parse", "--verify", integration_branch], check=False
        )

        if code == 0:
            logger.info(f"Integration branch {integration_branch} exists. Checking out...")
            await self._run_git(["checkout", integration_branch])
            try:
                await self._run_git(["pull"])
            except Exception as e:
                logger.warning(f"Pull failed on existing branch (perhaps no upstream): {e}")
                logger.info(f"Attempting to push {integration_branch} to origin...")
                try:
                    await self._run_git(["push", "-u", "origin", integration_branch])
                except Exception as push_err:
                    logger.error(f"Failed to push existing branch: {push_err}")
        else:
            logger.info(f"Creating new integration branch: {integration_branch}")
            await self._run_git(["checkout", "-b", integration_branch])
            await self._run_git(["push", "-u", "origin", integration_branch])

        return integration_branch

    async def create_feature_branch(self, branch_name: str, from_branch: str | None = None) -> str:
        """Creates and checks out a new feature branch from the specified base branch."""
        from_branch = from_branch or settings.DEFAULT_BASE_BRANCH

        logger.info(f"Creating feature branch: {branch_name} from {from_branch}")

        await self._auto_commit_if_dirty()

        # Ensure we're on the base branch and it's up to date
        await self._run_git(["checkout", from_branch])
        await self._run_git(["pull"])

        # Check if exists first
        _stdout, _stderr, code, _ = await self.runner.run_command(
            [self.git_cmd, "rev-parse", "--verify", branch_name], check=False
        )
        if code == 0:
            logger.info(f"Feature branch {branch_name} already exists. Checking out...")
            await self._run_git(["checkout", branch_name])
            try:
                await self._run_git(["pull"])
            except Exception as e:
                logger.warning(f"Pull failed on existing branch (perhaps no upstream): {e}")
                logger.info(f"Attempting to push {branch_name} to origin...")
                try:
                    await self._run_git(["push", "-u", "origin", branch_name])
                except Exception as push_err:
                    logger.error(f"Failed to push existing branch: {push_err}")
        else:
            await self._run_git(["checkout", "-b", branch_name])
            await self._run_git(["push", "-u", "origin", branch_name])

        logger.info(f"Created/verified and pushed feature branch: {branch_name}")
        return branch_name

    async def create_session_branch(
        self, session_id: str, branch_type: str, branch_id: str, integration_branch: str
    ) -> str:
        """Creates a session-scoped branch from integration branch."""
        branch_name = f"dev/{session_id}/{branch_type}{branch_id}"
        logger.info(f"Creating session branch: {branch_name} from {integration_branch}")

        await self._run_git(["checkout", integration_branch])
        await self._run_git(["pull"])

        _stdout, _stderr, code, _ = await self.runner.run_command(
            [self.git_cmd, "rev-parse", "--verify", branch_name], check=False
        )

        if code == 0:
            logger.info(f"Session branch {branch_name} exists. Checking out...")
            await self._run_git(["checkout", branch_name])
        else:
            logger.info(f"Creating new session branch: {branch_name}")
            await self._run_git(["checkout", "-b", branch_name])

        return branch_name

    async def validate_remote_branch(self, branch: str) -> tuple[bool, str]:
        """Validate that branch exists on remote and is up-to-date."""
        stdout, _stderr, code, _ = await self.runner.run_command(
            ["git", "ls-remote", "--heads", "origin", branch],
            check=False,
        )

        if code != 0 or not stdout.strip():
            return False, f"Branch '{branch}' does not exist on remote 'origin'"

        try:
            await self._run_git(["fetch", "origin", branch])
            local_hash = await self._run_git(["rev-parse", branch])
            remote_hash = await self._run_git(["rev-parse", f"origin/{branch}"])

            if local_hash != remote_hash:
                merge_base = await self._run_git(["merge-base", branch, f"origin/{branch}"])
                if merge_base == local_hash:
                    return False, (
                        f"Branch '{branch}' is behind remote.\n"
                        f"Pull latest changes: git pull origin {branch}"
                    )
                if merge_base == remote_hash:
                    logger.warning(f"Branch '{branch}' is ahead of remote (unpushed commits)")
                else:
                    return False, (
                        f"Branch '{branch}' has diverged from remote.\n"
                        f"Resolve divergence before proceeding."
                    )
        except Exception as e:
            logger.warning(f"Could not validate remote branch state: {e}")

        return True, ""

    # ------------------------------------------------------------------
    # Checkout / PR / Push / Diff  (originally checkout.py)
    # ------------------------------------------------------------------

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
        default_branch = settings.DEFAULT_BASE_BRANCH
        try:
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
            return default_branch
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
            branch = await self.get_current_branch()

            # Ensure tracking is set (fixes "no tracking information" warning)
            if branch:
                with contextlib.suppress(Exception):
                    await self._run_git(["branch", f"--set-upstream-to=origin/{branch}", branch])

            await self._run_git(["pull", "--rebase"])
            logger.info("Changes pulled successfully.")
        except Exception as e:
            logger.warning(f"pull --rebase failed ({e}). Aborting rebase to restore clean state.")
            try:
                await self._run_git(["rebase", "--abort"])
                logger.info("Rebase aborted successfully.")
            except Exception as abort_err:
                logger.warning(f"Could not abort rebase: {abort_err}")

            if self.cwd:
                logger.warning(
                    f"Fallback: Hard resetting to origin/{branch} in worktree to resolve conflict."
                )
                await self._run_git(["fetch", "origin", branch])
                await self._run_git(["reset", "--hard", f"origin/{branch}"])
            else:
                raise

    async def push_branch(self, branch: str, force: bool = False) -> None:
        """Pushes the specified branch to origin, skipping if already pushed in this batch."""
        global _pushed_commit_hashes

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
        target_branch = target_branch or settings.DEFAULT_BASE_BRANCH
        return await self._run_git(["diff", f"{target_branch}...HEAD"])

    async def get_changed_files(self, base_branch: str | None = None) -> list[str]:
        """Returns a list of unique file paths that have changed."""
        base_branch = base_branch or settings.DEFAULT_BASE_BRANCH

        files: set[str] = set()
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

    # ------------------------------------------------------------------
    # Merging  (originally merging.py)
    # ------------------------------------------------------------------

    async def _ensure_no_pending_merge(self) -> None:
        """Aborts any pending merge to ensure clean index."""
        merge_head = Path.cwd() / ".git" / "MERGE_HEAD"
        if merge_head.exists():
            logger.warning("Pending merge detected. Aborting to clean index...")
            try:
                await self._run_git(["merge", "--abort"], check=False)
            except Exception as e:
                logger.warning(f"Failed to abort merge: {e}")

        for fname in ["CHERRY_PICK_HEAD", "REVERT_HEAD"]:
            fpath = Path.cwd() / ".git" / fname
            if fpath.exists():
                logger.warning(f"Pending {fname} detected. Aborting...")
                await self._run_git(["quit"], check=False)

    def _validate_branch_name(self, branch_name: str) -> None:
        """Validates branch names to prevent command injection or unintended git behavior."""
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
        original_branch = await self.get_current_branch()

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

        await self.push_branch(integration_branch)

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

    # ------------------------------------------------------------------
    # State branch management  (originally state.py)
    # ------------------------------------------------------------------

    async def ensure_state_branch(self) -> None:
        """Ensures the orphan branch exists."""
        _stdout, _stderr, code, _ = await self.runner.run_command(
            [self.git_cmd, "rev-parse", "--verify", self.STATE_BRANCH], check=False
        )
        if code == 0:
            return

        logger.info(f"Checking remote for {self.STATE_BRANCH}...")
        await self._run_git(
            ["fetch", "origin", f"{self.STATE_BRANCH}:{self.STATE_BRANCH}"], check=False
        )

        _stdout, _stderr, code, _ = await self.runner.run_command(
            [self.git_cmd, "rev-parse", "--verify", self.STATE_BRANCH], check=False
        )
        if code == 0:
            return

        logger.info(f"Creating orphan branch: {self.STATE_BRANCH}")
        with tempfile.TemporaryDirectory():
            process = await asyncio.create_subprocess_exec(
                self.git_cmd,
                "mktree",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await process.communicate(input=b"")
            if process.returncode != 0:
                err_msg = f"git mktree failed: {stderr_bytes.decode()}"
                raise RuntimeError(err_msg)

            empty_tree = stdout_bytes.decode().strip()
            process = await asyncio.create_subprocess_exec(
                self.git_cmd,
                "commit-tree",
                empty_tree,
                "-m",
                "Initial state branch",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await process.communicate()
            if process.returncode != 0:
                err_msg = f"git commit-tree failed: {stderr_bytes.decode()}"
                raise RuntimeError(err_msg)

            commit_hash = stdout_bytes.decode().strip()
            await self._run_git(
                ["update-ref", f"refs/heads/{self.STATE_BRANCH}", commit_hash], check=True
            )

    async def read_state_file(self, filename: str) -> str | None:
        """Reads a file from the state branch."""
        try:
            content, _stderr, code, _ = await self.runner.run_command(
                [self.git_cmd, "show", f"{self.STATE_BRANCH}:{filename}"], check=False
            )
            return str(content) if code == 0 else None
        except Exception:
            return None

    async def save_state_file(self, filename: str, content: str, message: str) -> None:
        """Saves a file to the state branch using a temporary worktree."""
        await self.ensure_state_branch()
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                await self._run_git(
                    ["worktree", "add", tmp_dir, self.STATE_BRANCH], check=True
                )
            except RuntimeError:
                await self._run_git(["worktree", "prune"], check=False)
                await self._run_git(
                    ["worktree", "add", "-f", tmp_dir, self.STATE_BRANCH], check=True
                )

            try:
                file_path = Path(tmp_dir) / filename
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
                await self._run_git(["-C", tmp_dir, "add", filename], check=True)

                status, _stderr, _code, _ = await self.runner.run_command(
                    [self.git_cmd, "-C", tmp_dir, "status", "--porcelain"], check=False
                )
                if status.strip():
                    await self._run_git(["-C", tmp_dir, "commit", "-m", message], check=True)
                    await self._run_git(
                        ["-C", tmp_dir, "push", "origin", self.STATE_BRANCH], check=False
                    )
            finally:
                await self._run_git(["worktree", "remove", "--force", tmp_dir], check=False)


# ==============================================================================
# GitWorktreeManager — ephemeral worktrees for parallel execution isolation
# ==============================================================================


class GitWorktreeManager:
    """Manages ephemeral Git worktrees for parallel execution isolation."""

    def __init__(self, worktree_root: str = "logs/worktrees") -> None:
        self.runner = ProcessRunner()
        self.git_cmd = "git"
        self.gh_cmd = settings.tools.gh_cmd
        self.cwd: Path | None = None
        self.worktree_root = Path(worktree_root)

    async def _run_git(self, args: list[str], check: bool = True) -> str:
        """Core git execution (same pattern as GitManager)."""
        for attempt in range(5):
            cmd = [self.git_cmd, *args]
            stdout, stderr, code, _ = await self.runner.run_command(cmd, cwd=self.cwd, check=False)
            error_msg = str(stderr).strip() or str(stdout).strip()
            if code == 0:
                return str(stdout).strip()
            if "index.lock" in error_msg and attempt < 4:
                _logger = logging.getLogger(__name__)
                _logger.warning(f"Index locked, retrying {redact_secrets(' '.join(args))}...")
                await asyncio.sleep(random.SystemRandom().uniform(0.5, 2.0))
                continue
            if code != 0 and check:
                msg = f"Git command failed: {redact_secrets(' '.join(cmd))} - Stderr: {error_msg}"
                raise RuntimeError(msg)
            return str(stdout).strip()
        return ""

    async def create_worktree(self, cycle_id: str, branch_name: str) -> Path:
        """Creates a new worktree for a specific cycle and branch."""
        worktree_path = self.worktree_root / f"cycle_{cycle_id}"

        # Cleanup existing if any (shouldn't happen but for robustness)
        if worktree_path.exists():
            await self.remove_worktree(cycle_id)

        self.worktree_root.mkdir(parents=True, exist_ok=True)

        logger.info(f"Creating Git Worktree for cycle {cycle_id} on branch {branch_name}...")
        try:
            temp_branch = f"isolated-cycle-{cycle_id}-{branch_name}"
            await self._run_git(
                ["worktree", "add", "-b", temp_branch, str(worktree_path), branch_name], check=True
            )
            logger.info(f"✓ Isolated worktree created at {worktree_path} on branch {temp_branch}")
            return worktree_path.absolute()
        except Exception as e:
            logger.error(f"Failed to create worktree: {e}")
            if worktree_path.exists():
                shutil.rmtree(worktree_path, ignore_errors=True)
            raise

    async def remove_worktree(self, cycle_id: str) -> None:
        """Removes a worktree and cleans up the directory."""
        worktree_path = self.worktree_root / f"cycle_{cycle_id}"
        if not worktree_path.exists():
            await self._run_git(["worktree", "prune"], check=False)
            return

        logger.info(f"Removing Git Worktree for cycle {cycle_id}...")
        try:
            await self._run_git(["worktree", "remove", str(worktree_path), "--force"], check=False)
            await self._run_git(["worktree", "prune"], check=False)

            stdout = await self._run_git(
                ["branch", "--list", f"isolated-cycle-{cycle_id}-*"], check=False
            )
            if stdout:
                branches = [b.strip().replace("* ", "") for b in stdout.split("\n") if b.strip()]
                for b in branches:
                    await self._run_git(["branch", "-D", b], check=False)
                    logger.info(f"✓ Deleted temporary branch {b}")
        except Exception as e:
            logger.warning(f"Failed to remove worktree gracefully: {e}")

        if worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)
        logger.info(f"✓ Worktree directory {worktree_path} cleaned up.")
