import re
from pathlib import Path

from src.domain_models import ConflictRegistryItem
from src.process_runner import ProcessRunner
from src.utils import logger


class ConflictMarkerRemainsError(Exception):
    """Raised when a file still contains Git conflict markers."""


class ConflictManager:
    """Extracts and validates Git conflict markers."""

    def __init__(self, runner: ProcessRunner | None = None) -> None:
        self.runner = runner or ProcessRunner()
        self.conflict_marker_pattern = re.compile(r"^(<{7}\s.*|={7}|>{7}\s.*)$", re.MULTILINE)

    def _validate_path(self, path: Path) -> Path:
        """
        Validates that a path is safe and within the workspace root.
        Returns the resolved path if safe, otherwise raises ValueError.
        """
        from src.config import settings

        try:
            # .resolve() handles '..' components and symlinks.
            # strict=False allows resolving even if the file doesn't exist yet.
            resolved = path.resolve(strict=False)
            root = settings.paths.workspace_root.resolve(strict=True)
        except (ValueError, RuntimeError) as e:
            msg = f"Security validation failed for path {path}: {e}"
            raise ValueError(msg) from e

        if not resolved.is_relative_to(root):
            msg = f"Path {path} (resolved to {resolved}) escapes workspace root {root}"
            raise ValueError(msg)

        return resolved

    def _get_unmerged_files(self, stdout: str) -> list[str]:
        """Extracts unmerged file paths from git status output."""
        from src.config import settings

        unmerged_files = []
        conflict_codes = settings.tools.conflict_codes
        for line in stdout.splitlines():
            if len(line) >= 3 and line[:2] in conflict_codes:
                unmerged_files.append(line[3:])
        return unmerged_files

    async def scan_conflicts(self, repo_path: Path) -> list[ConflictRegistryItem]:
        """
        Scans the repository for files with standard git conflict markers and
        returns a list of ConflictRegistryItem objects representing them.
        """
        try:
            repo_path = self._validate_path(repo_path)
            from src.config import settings

            git_cmd = settings.tools.git_cmd

            # Use git status --porcelain to find unmerged files quickly
            stdout, _, _, _ = await self.runner.run_command(
                [git_cmd, "status", "--porcelain"],
                cwd=repo_path,
                check=False,
            )
        except Exception as e:
            logger.error(f"Error scanning for conflicts: {e}")
            return []

        unmerged_files = self._get_unmerged_files(stdout)
        registry_items = []
        for file_path_str in unmerged_files:
            item = self._process_unmerged_file(repo_path, file_path_str)
            if item:
                registry_items.append(item)

        return registry_items

    def _process_unmerged_file(
        self, repo_path: Path, file_path_str: str
    ) -> ConflictRegistryItem | None:
        """Processes a single unmerged file to detect conflict markers."""
        try:
            # Security: Validate each file path joined with repo_path
            file_path = self._validate_path(repo_path / file_path_str)
        except ValueError:
            logger.warning(f"Skipping unsafe file path during conflict scan: {file_path_str}")
            return None

        if not file_path.exists() or not file_path.is_file():
            return None

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None  # Skip binary files
        except (FileNotFoundError, PermissionError) as e:
            logger.warning(f"Could not read {file_path} during conflict scan: {e}")
            return None

        markers = self.conflict_marker_pattern.findall(content)
        if markers:
            return ConflictRegistryItem(
                file_path=file_path_str,
                conflict_markers=markers,
            )
        return None

    def validate_resolution(self, file_path: Path) -> bool:
        """
        Reads the given file and returns False if any standard git conflict
        markers remain.
        """
        try:
            file_path = self._validate_path(file_path)
        except ValueError as e:
            logger.warning(f"Path validation failed for {file_path}: {e}")
            return True

        if not file_path.exists() or not file_path.is_file():
            return True

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return True  # Binary files handled differently, assume True for text based check
        except (FileNotFoundError, PermissionError) as e:
            logger.warning(f"Could not read {file_path} during conflict validation: {e}")
            return True

        if self.conflict_marker_pattern.search(content):
            err_msg = f"File {file_path} still contains git conflict markers."
            raise ConflictMarkerRemainsError(err_msg)

        return True

    async def build_conflict_package(self, item: ConflictRegistryItem, repo_path: Path) -> str:
        """
        Builds the conflict resolution prompt package for the Jules Master Integrator session.
        Extracts Base, Local (Branch A), and Remote (Branch B) versions using Git 3-Way Diff.
        """
        repo_path = self._validate_path(repo_path)
        try:
            from src.config import settings

            git_cmd = settings.tools.git_cmd
        except (ImportError, AttributeError):
            git_cmd = "git"

        async def _get_git_version(stage: int) -> str:
            try:
                # stage is int, item.file_path is validated indirectly by being in repo_path
                stdout, _stderr, _returncode, _ = await self.runner.run_command(
                    [git_cmd, "show", f":{stage}:{item.file_path}"],
                    cwd=repo_path,
                    check=True,
                )
                return stdout.strip()
            except Exception:
                return "<FILE_NOT_IN_BASE>" if stage == 1 else ""

        base_code = await _get_git_version(1)
        local_code = await _get_git_version(2)
        remote_code = await _get_git_version(3)

        # Read specific instructions from MASTER_INTEGRATOR_PROMPT.md if available
        try:
            from src.config import settings

            prompt_template = settings.read_template(
                "MASTER_INTEGRATOR_PROMPT.md",
                default=(
                    "You are the Master Integrator. Resolve the Git conflicts in this file.\n"
                    "Do not just pick A or B; understand the intent of both branches.\n"
                    "Apply DRY principles. Return the completely unified file without any `<<<<<<<` markers.\n"
                    "Respond ONLY with the strictly validated JSON schema requested."
                ),
            )
        except Exception:
            prompt_template = ""

        return f"""{prompt_template}

###################
File: {item.file_path}

### Base (元のコード)
```python
{base_code}
```

### Branch A の変更 (Local)
```python
{local_code}
```

### Branch B の変更 (Remote)
```python
{remote_code}
```
"""
