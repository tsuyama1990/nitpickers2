from pathlib import Path

from src.process_runner import ProcessRunner
from src.services.git_ops import GitManager
from src.utils import logger


class DependencyManager:
    """Manages project dependencies and git initialization."""

    def __init__(self) -> None:
        self.runner = ProcessRunner()
        self.git = GitManager()

    async def initialize_dependencies_and_git(self) -> None:
        if not (Path.cwd() / "pyproject.toml").exists():
            logger.info("Initializing pyproject.toml...")
            await self.runner.run_command(["uv", "init", "--no-workspace"], check=False)

        # Smart dependency check to avoid 'ambiguous update' errors in uv
        pyproject_content = ""
        try:
            pyproject_content = (Path.cwd() / "pyproject.toml").read_text()
        except Exception:
            pass

        deps_to_add = []
        for dep in ["ruff", "mypy", "pytest", "pytest-cov", "pre-commit"]:
            if dep not in pyproject_content:
                deps_to_add.append(dep)

        if deps_to_add:
            logger.info(f"Adding missing development dependencies ({', '.join(deps_to_add)})...")
            try:
                await self.runner.run_command(
                    ["uv", "add", "--dev", "--no-sync", *deps_to_add],
                    check=True,
                )
                logger.info("✓ Dependencies added to pyproject.toml successfully.")
            except Exception as e:
                logger.warning(f"Failed to install dependencies: {e}")
        else:
            logger.info("Core development dependencies already present in pyproject.toml.")

        if not (Path.cwd() / ".git").exists():
            logger.info("Initializing Git repository...")
            await self.runner.run_command(["git", "init"], check=True)

        # Ensure docker container can safely access the repository
        try:
            await self.runner.run_command(
                ["git", "config", "--global", "--add", "safe.directory", "/app"], check=False
            )
        except Exception as e:
            logger.warning(f"Failed to configure git safe.directory: {e}")

        try:
            await self.git.add_all()

            if await self.git.commit_changes(
                "Initialize project with Nitpick structure and dev dependencies"
            ):
                logger.info("✓ Changes committed.")

                try:
                    remote_url = await self.git.get_remote_url()
                    if remote_url:
                        current_branch = await self.git.get_current_branch()
                        logger.info(f"Pushing {current_branch} to origin...")
                        await self.git.push_branch(current_branch)
                        logger.info("✓ Successfully pushed to remote.")
                    else:
                        logger.info("No remote 'origin' configured. Skipping push.")
                except Exception as e:
                    logger.warning(f"Failed to push to remote: {e}")
            else:
                logger.info("No changes to commit.")

        except Exception as e:
            logger.warning(f"Git operations failed: {e}")

    async def sync_dependencies(self) -> None:
        """Syncs dependencies using uv."""
        logger.info("[ProjectManager] Syncing dependencies...")
        try:
            await self.runner.run_command(["uv", "sync", "--dev"], check=True)

            _stdout, _stderr, code_ruff, _ = await self.runner.run_command(
                ["uv", "run", "ruff", "--version"], check=False
            )
            _stdout, _stderr, code_mypy, _ = await self.runner.run_command(
                ["uv", "run", "mypy", "--version"], check=False
            )

            if code_ruff != 0 or code_mypy != 0:
                logger.info("[ProjectManager] Installing missing linters...")
                await self.runner.run_command(["uv", "add", "--dev", "ruff", "mypy"], check=True)

            logger.info("[ProjectManager] Environment prepared.")
        except Exception as e:
            logger.warning(f"[ProjectManager] Dependency sync failed: {e}")
