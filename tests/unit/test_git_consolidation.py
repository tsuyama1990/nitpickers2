"""Unit tests verifying Git consolidation completeness.

This test captures the full method inventory of GitManager BEFORE the
consolidation refactoring (6 files → 1 file). Running this test after
the refactoring confirms no methods were accidentally dropped.

Method inventory derived from:
  - src/services/git/base.py      (8 methods)
  - src/services/git/branching.py (5 methods)
  - src/services/git/checkout.py  (13 methods, including get_current_branch duplicate)
  - src/services/git/merging.py   (5 methods, 1 static)
  - src/services/git/state.py     (3 methods)
  - src/services/git/worktree.py  (2 methods + __init__)

Total public API methods tracked here: 33 (excluding __init__, _private helpers)
"""


import typing

from src.services.git_ops import GitManager, GitWorktreeManager

# ---------------------------------------------------------------------------
#  GitManager method inventory
# ---------------------------------------------------------------------------

class TestGitManagerMethodInventory:
    """Verifies ALL methods exist on GitManager after consolidation."""

    # ── From base.py ──
    def test_base_methods_exist(self) -> None:
        gm = GitManager()
        assert hasattr(gm, '_run_git')
        assert hasattr(gm, '_ensure_no_lock')
        assert hasattr(gm, 'get_current_commit')
        assert hasattr(gm, 'get_status')
        assert hasattr(gm, 'add_all')
        assert hasattr(gm, 'commit')
        assert hasattr(gm, 'fetch_changes')
        assert hasattr(gm, 'reset_hard')
        assert hasattr(gm, '_auto_commit_if_dirty')

    # ── From branching.py ──
    def test_branching_methods_exist(self) -> None:
        gm = GitManager()
        assert hasattr(gm, 'get_current_branch')
        assert hasattr(gm, 'get_remote_url')
        assert hasattr(gm, 'create_integration_branch')
        assert hasattr(gm, 'create_feature_branch')
        assert hasattr(gm, 'create_session_branch')
        assert hasattr(gm, 'validate_remote_branch')

    # ── From checkout.py ──
    def test_checkout_methods_exist(self) -> None:
        gm = GitManager()
        assert hasattr(gm, '_checkout_pr')
        assert hasattr(gm, '_checkout_branch')
        assert hasattr(gm, 'smart_checkout')
        assert hasattr(gm, 'checkout_pr')
        assert hasattr(gm, 'get_pr_base_branch')
        assert hasattr(gm, 'checkout_branch')
        assert hasattr(gm, 'ensure_clean_state')
        assert hasattr(gm, 'commit_changes')
        assert hasattr(gm, 'pull_changes')
        assert hasattr(gm, 'push_branch')
        assert hasattr(gm, 'get_diff')
        assert hasattr(gm, 'get_changed_files')

    # ── From merging.py ──
    def test_merging_methods_exist(self) -> None:
        gm = GitManager()
        assert hasattr(gm, '_ensure_no_pending_merge')
        assert hasattr(gm, '_validate_branch_name')
        assert hasattr(gm, 'safe_merge_with_conflicts')
        assert hasattr(gm, 'merge_branch')
        assert hasattr(gm, 'merge_pr')
        assert hasattr(gm, 'create_final_pr')

    # ── From state.py ──
    def test_state_methods_exist(self) -> None:
        gm = GitManager()
        assert hasattr(gm, 'ensure_state_branch')
        assert hasattr(gm, 'read_state_file')
        assert hasattr(gm, 'save_state_file')

    # ── Constants ──
    def test_constants_exist(self) -> None:
        assert GitManager.STATE_BRANCH == "ac-cdd/state", (
            f"Expected STATE_BRANCH='ac-cdd/state', got '{GitManager.STATE_BRANCH}'"
        )


# ---------------------------------------------------------------------------
#  GitWorktreeManager method inventory
# ---------------------------------------------------------------------------

class TestGitWorktreeManagerMethodInventory:
    """Verifies ALL methods exist on GitWorktreeManager after consolidation."""

    def test_worktree_methods_exist(self) -> None:
        wtm = GitWorktreeManager()
        assert hasattr(wtm, 'create_worktree')
        assert hasattr(wtm, 'remove_worktree')

    def test_worktree_can_be_instantiated(self) -> None:
        """GitWorktreeManager has its own __init__ with worktree_root param."""
        wtm = GitWorktreeManager(worktree_root="custom/worktrees")
        assert wtm.worktree_root is not None


# ---------------------------------------------------------------------------
#  Module-level exports
# ---------------------------------------------------------------------------

class TestGitOpsModuleExports:
    """Verify that module-level globals are preserved."""

    def test_workspace_lock_exists(self) -> None:
        import asyncio

        from src.services.git_ops import workspace_lock
        assert isinstance(workspace_lock, asyncio.Lock)

    def test_pushed_commit_hashes_exists(self) -> None:
        from src.services.git_ops import _pushed_commit_hashes
        assert isinstance(_pushed_commit_hashes, dict)

    def test_git_manager_and_worktree_exported(self) -> None:
        """Both classes should be importable from git_ops."""
        from src.services.git_ops import GitManager, GitWorktreeManager
        assert GitManager is not None
        assert GitWorktreeManager is not None


# ---------------------------------------------------------------------------
#  GitManager initialization
# ---------------------------------------------------------------------------

class TestGitManagerInit:
    """Verify __init__ properly sets up all attributes."""

    def test_default_init_sets_runner_and_cwd(self) -> None:
        gm = GitManager()
        assert gm.runner is not None
        assert gm.git_cmd == "git"
        assert gm.cwd is None

    def test_init_with_cwd(self) -> None:
        from pathlib import Path
        cwd = Path("/tmp/test_dir_dir")  # noqa: S108  # noqa: S108
        gm = GitManager(cwd=cwd)
        assert gm.cwd == cwd

    def test_gh_cmd_from_settings(self) -> None:
        """gh_cmd should be populated from settings (mocked in conftest)."""
        gm = GitManager()
        assert gm.gh_cmd is not None


# ---------------------------------------------------------------------------
#  Callable verification
# ---------------------------------------------------------------------------

class TestGitManagerMethodsAreCallable:
    """Verify all methods are callable (not just attributes)."""

    ALL_PUBLIC_METHODS: typing.ClassVar[list[str]] = [
        'get_current_commit', 'get_status', 'add_all', 'commit',
        'fetch_changes', 'reset_hard',
        'get_current_branch', 'get_remote_url',
        'create_integration_branch', 'create_feature_branch',
        'create_session_branch', 'validate_remote_branch',
        'smart_checkout', 'checkout_pr', 'get_pr_base_branch',
        'checkout_branch', 'ensure_clean_state', 'commit_changes',
        'pull_changes', 'push_branch', 'get_diff', 'get_changed_files',
        'safe_merge_with_conflicts', 'merge_branch', 'merge_pr',
        'create_final_pr',
        'ensure_state_branch', 'read_state_file', 'save_state_file',
    ]

    def test_all_public_methods_are_callable(self) -> None:
        gm = GitManager()
        for name in self.ALL_PUBLIC_METHODS:
            attr = getattr(gm, name)
            assert callable(attr), f"GitManager.{name} is not callable (type={type(attr)})"
