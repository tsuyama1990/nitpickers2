# P1: `src/services/git/` Directory Consolidation Plan

> **Objective**: Merge 6 Git mixin files (~970 lines) into a single `git_ops.py` file,
> removing the over-engineered mixin pattern while preserving all functionality.

## 1. Current Architecture

```
src/services/git/                    (6 files, ~970 lines)
├── base.py         (153 lines)      BaseGitManager — core _run_git(), add/commit/reset
├── branching.py    (149 lines)      GitBranchingMixin — branch create/validate
├── checkout.py     (236 lines)      GitCheckoutMixin — checkout/PR/pull/push/diff
├── merging.py      (246 lines)      GitMergingMixin — merge/PR merge/final PR
├── state.py        (111 lines)      GitStateMixin — state branch management
└── worktree.py     (75 lines)       GitWorktreeManager — standalone worktree class

src/services/git_ops.py              (30 lines)     GitManager(mixins) + globals
```

### Class Hierarchy

```
BaseGitManager (base.py)
  ├── GitBranchingMixin (branching.py)
  ├── GitCheckoutMixin (checkout.py)
  ├── GitMergingMixin (merging.py)
  ├── GitStateMixin (state.py)
  └── GitWorktreeManager (worktree.py)  ← standalone class, NOT a mixin of GitManager

GitManager (git_ops.py) = GitBranchingMixin + GitCheckoutMixin + GitMergingMixin + GitStateMixin + BaseGitManager
```

### All Callers of These Classes

| Class | Import Source | Callers |
|-------|--------------|---------|
| `GitManager` | `src.services.git_ops` | `service_container.py`, `graph_nodes.py`, `validators.py`, `architect.py`, `critic_nodes.py`, `auditor_usecase.py`, `uat_usecase.py`, `jules_client.py`, `workflow.py`, `qa_usecase.py`, `dependency_manager.py`, `jules/git_context.py`, `jules/context_builder.py` |
| `GitWorktreeManager` | `src.services.git.worktree` | `workflow.py` (line 642) |
| `workspace_lock` | `src.services.git_ops` | `auditor_usecase.py`, `workflow.py` |
| `BaseGitManager` | internal only | Only used via inheritance |

## 2. Target Architecture

```
src/services/git_ops.py              (~1000 lines)  ALL git code in one file
├── Globals: workspace_lock, _pushed_commit_hashes
├── GitManager — all methods directly defined:
│   ├── __init__(), _ensure_no_lock(), _run_git()              ← from base.py
│   ├── get_current_commit(), get_status(), add_all()          ← from base.py
│   ├── commit(), fetch_changes(), reset_hard()                ← from base.py
│   ├── _auto_commit_if_dirty()                                ← from base.py
│   ├── get_current_branch(), get_remote_url()                 ← from branching.py
│   ├── create_integration_branch(), create_feature_branch()   ← from branching.py
│   ├── create_session_branch(), validate_remote_branch()      ← from branching.py
│   ├── _checkout_pr(), _checkout_branch(), smart_checkout()   ← from checkout.py
│   ├── checkout_pr(), get_pr_base_branch(), checkout_branch() ← from checkout.py
│   ├── ensure_clean_state(), commit_changes(), pull_changes() ← from checkout.py
│   ├── push_branch(), get_diff(), get_changed_files()         ← from checkout.py
│   ├── _ensure_no_pending_merge(), _validate_branch_name()    ← from merging.py
│   ├── safe_merge_with_conflicts(), merge_branch()            ← from merging.py
│   ├── merge_pr(), create_final_pr()                          ← from merging.py
│   ├── ensure_state_branch(), read_state_file()               ← from state.py
│   └── save_state_file()                                      ← from state.py
├── GitWorktreeManager — standalone class:
│   ├── __init__(), create_worktree(), remove_worktree()       ← from worktree.py
└── (src/services/git/ directory deleted)
```

## 3. Step-by-Step Implementation

### Step 1: Merge all 6 files into `git_ops.py`
- Open the current `git_ops.py` (30 lines)
- Append ALL code from the 6 files in logical order:
  1. Imports (de-dup across all files)
  2. Globals (`workspace_lock`, `_pushed_commit_hashes`)
  3. `GitManager` class with ALL methods from all mixins + base
  4. `GitWorktreeManager` class

**Critical merge rules:**
- `get_current_branch()` appears in BOTH `branching.py:9` and `checkout.py:176` with DIFFERENT implementations:
  - `branching.py`: uses `_run_git(["rev-parse", "--abbrev-ref", "HEAD"])` with try/except returning "main"
  - `checkout.py`: uses `runner.run_command([git_cmd, "branch", "--show-current"])` without try/except
  - ➡️ **Keep both**: the `branching.py` version is used internally (robust), the `checkout.py` version is the canonical one
  - Actually, looking more carefully: the `branching.py` version at line 9 is a DIFFERENT method from the one in `checkout.py` line 176. Let me re-check...
  - `branching.py:9` is in `GitBranchingMixin` and is `async def get_current_branch(self) -> str`
  - `checkout.py:176` is in `GitCheckoutMixin` and is ALSO `async def get_current_branch(self) -> str`
  - Since Python MRO resolves this, the one in `GitBranchingMixin` (first in inheritance) would be used
  - Actually wait - `GitManager(GitBranchingMixin, GitCheckoutMixin, ...)` - so `GitBranchingMixin` wins
  - But the `checkout.py` version is used internally within that mixin's own methods via `self.get_current_branch()`
  - Since it's the same class after merge, there's only ONE method. We need to pick one implementation or merge them.
  - The `branching.py` version is more robust (has try/except fallback). Let's keep the more robust version.
  - However, the `checkout.py` version is also called from within `pull_changes()` at line 147.
  - **Decision**: Keep the more robust `branching.py` version. Both will work since it's all one class.

- `STATE_BRANCH` constant appears in BOTH `git_ops.py:26` and `state.py:13`:
  - `git_ops.py:26`: `STATE_BRANCH = "ac-cdd/state"` (re-exposed on GitManager)
  - `state.py:13`: `STATE_BRANCH = "src/state"` (on GitStateMixin, with comment about name)
  - `state.py:17`: `STATE_BRANCH_NAME = "ac-cdd/state"` (the actual branch name used)
  - **Decision**: Keep the actual branch name `"ac-cdd/state"` on `GitManager`

### Step 2: Avoid naming collisions
- `_checkout_pr` and `_checkout_branch`: private helpers from `checkout.py` — keep as private methods
- `_ensure_no_pending_merge` from `merging.py` — keep as private method
- `_auto_commit_if_dirty` from `base.py` — keep as private method
- `_validate_branch_name` from `merging.py` — keep as private method
- `_pushed_commit_hashes` — referenced in `checkout.py:push_branch()` → becomes local to `git_ops.py`

### Step 3: Update `workflow.py` imports
Change line 642 from:
```python
from src.services.git.worktree import GitWorktreeManager
```
to:
```python
from src.services.git_ops import GitWorktreeManager
```

### Step 4: Delete `src/services/git/` directory
Remove all 6 files. The `__init__.py` doesn't exist, so no need to worry about it.

### Step 5: Run tests
```bash
pytest tests/integration/test_git_robustness.py -v
pytest tests/unit/ -v
```

## 4. Merge Strategy (Textual Order in New `git_ops.py`)

The merged file will follow this structure:

```python
"""Consolidated Git operations module."""
# === IMPORTS ===
# (collected from all 6 files, de-duplicated)

# === MODULE-LEVEL GLOBALS ===
workspace_lock = asyncio.Lock()
_pushed_commit_hashes: dict[str, str] = {}

# === GitManager CLASS ===
class GitManager:
    """Manages Git operations for the AC-CDD workflow."""

    STATE_BRANCH = "ac-cdd/state"

    def __init__(self, cwd: Path | None = None) -> None:
        ...  # from base.py

    # --- Core git execution (base.py) ---
    async def _ensure_no_lock(self) -> None: ...
    async def _run_git(self, args, check=True) -> str: ...
    async def get_current_commit(self) -> str: ...
    async def get_status(self) -> str: ...
    async def add_all(self) -> None: ...
    async def commit(self, message: str) -> None: ...
    async def fetch_changes(self) -> None: ...
    async def reset_hard(self) -> None: ...
    async def _auto_commit_if_dirty(self, message="Auto-save") -> None: ...

    # --- Branching (branching.py) ---
    async def get_current_branch(self) -> str: ...
    async def get_remote_url(self) -> str: ...
    async def create_integration_branch(self, ...) -> str: ...
    async def create_feature_branch(self, ...) -> str: ...
    async def create_session_branch(self, ...) -> str: ...
    async def validate_remote_branch(self, branch) -> tuple[bool, str]: ...

    # --- Checkout / PR / Push / Diff (checkout.py) ---
    async def _checkout_pr(self, target, force) -> None: ...
    async def _checkout_branch(self, target, force) -> None: ...
    async def smart_checkout(self, target, is_pr=False, force=False) -> None: ...
    async def checkout_pr(self, pr_url) -> None: ...
    async def get_pr_base_branch(self, pr_url) -> str: ...
    async def checkout_branch(self, branch_name, force=False) -> None: ...
    async def ensure_clean_state(self, force_stash=False) -> None: ...
    async def commit_changes(self, message) -> bool: ...
    async def pull_changes(self) -> None: ...
    async def push_branch(self, branch, force=False) -> None: ...
    async def get_diff(self, target_branch=None) -> str: ...
    async def get_changed_files(self, base_branch=None) -> list[str]: ...

    # --- Merging (merging.py) ---
    async def _ensure_no_pending_merge(self) -> None: ...
    def _validate_branch_name(self, branch_name) -> None: ...
    async def safe_merge_with_conflicts(self, branch_name) -> bool: ...
    async def merge_branch(self, target, source) -> None: ...
    async def merge_pr(self, pr_number, method="squash") -> None: ...
    async def create_final_pr(self, integration_branch, title, body) -> str: ...

    # --- State branch (state.py) ---
    async def ensure_state_branch(self) -> None: ...
    async def read_state_file(self, filename) -> str | None: ...
    async def save_state_file(self, filename, content, message) -> None: ...

# === GitWorktreeManager CLASS ===
class GitWorktreeManager:
    """Manages ephemeral Git worktrees for parallel execution isolation."""
    def __init__(self, worktree_root="logs/worktrees") -> None: ...
    async def create_worktree(self, cycle_id, branch_name) -> Path: ...
    async def remove_worktree(self, cycle_id) -> None: ...
```

## 5. Risk Assessment

| Risk | Level | Mitigation |
|------|-------|------------|
| Method resolution order changes | 🟢 Low | All methods become direct members of `GitManager`; no MRO ambiguity |
| `self.runner` access from mixin methods | 🟢 Low | `runner` is set in `__init__`, still accessible via `self.runner` |
| `get_current_branch()` duplicate implementation | 🟢 Low | Keep the robust version (with try/except), both return the same thing |
| `STATE_BRANCH` constant duplication | 🟢 Low | Only one constant needed on `GitManager` |
| Internal cross-references (`self.get_current_branch()`) | 🟢 Low | All methods become siblings on same class |
| `self.cwd` usage in `pull_changes()` worktree fallback | 🟢 Low | `cwd` set in `__init__`, still accessible |
| `GitWorktreeManager` imported as class reference | 🟢 Low | Just change import path in `workflow.py` |
| `_pushed_commit_hashes` import in `checkout.py` | 🟢 Low | Becomes intra-module reference |

## 6. Verification Plan

1. Run the Git robustness tests:
   ```
   pytest tests/integration/test_git_robustness.py -v
   ```
2. Run the full unit test suite:
   ```
   pytest tests/unit/ -v
   ```
3. Run integration tests:
   ```
   pytest tests/integration/ -v
   ```
4. Run all tests (excluding live e2e):
   ```
   pytest --ignore=tests/e2e/live -v
   ```
