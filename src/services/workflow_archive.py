"""Session artifact archiving and phase transitions.

Split from workflow.py — part of WorkflowService decomposition.
"""

import asyncio
import contextlib
import shutil
from pathlib import Path

import anyio
from rich.console import Console

from src.state_manager import StateManager

console = Console()


class WorkflowArchiver:
    """Session artifact archiving and phase management.

    Mixin class — depends on self being a WorkflowService instance
    that provides self.git.
    """

    async def _archive_and_reset_state(self) -> None:
        """
        Archives current session artifacts to dev_documents/system_prompts_phaseNN
        and resets the state for the next phase safely.
        """
        from src.config import settings

        docs_dir = settings.paths.documents_dir
        if not await asyncio.to_thread(docs_dir.exists):
            return

        next_phase_num = self._get_next_phase_num(docs_dir)
        dir_name = settings.ARCHIVE_DIR_TEMPLATE.format(phase_num=next_phase_num)
        phase_dir = docs_dir / dir_name
        console.print(f"\n[bold cyan]Archiving session artifacts to {phase_dir}...[/bold cyan]")

        try:
            await self._archive_files(docs_dir, phase_dir)
            self._reset_project_state(phase_dir)
            self._prepare_next_phase(docs_dir)
            await self._commit_archived_phase(next_phase_num)
        except Exception as e:
            from src.utils import logger

            logger.error(f"Failed during archive and reset state: {e}")

        console.print("[green]Created fresh, empty ALL_SPEC.md for the next phase.[/green]")
        console.print(f"[green]Ready for Phase {next_phase_num + 1}![/green]")

    def _get_next_phase_num(self, docs_dir: Path) -> int:
        existing_phases = [
            d
            for d in docs_dir.iterdir()
            if d.is_dir() and d.name.startswith("system_prompts_phase")
        ]
        nums: list[int] = []
        for d in existing_phases:
            with contextlib.suppress(IndexError, ValueError):
                nums.append(int(d.name.split("_phase")[1]))
        return max(nums) + 1 if nums else 1

    async def _safe_move_item(self, src: Path, dest: Path) -> None:
        if not await asyncio.to_thread(src.exists):
            return
        await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
        try:
            await self.git._run_git(  # type: ignore[attr-defined]
                ["mv", str(src), str(dest)]
            )  # Keeping _run_git for mv as there's no public method yet
        except Exception:
            try:
                await asyncio.to_thread(src.replace, dest)
            except OSError:
                await asyncio.to_thread(shutil.move, str(src), str(dest))

    async def _archive_files(self, docs_dir: Path, phase_dir: Path) -> None:
        sys_prompts_dir = docs_dir / "system_prompts"
        if await asyncio.to_thread(sys_prompts_dir.exists):
            await self._safe_move_item(sys_prompts_dir, phase_dir)
        else:
            await asyncio.to_thread(phase_dir.mkdir, parents=True, exist_ok=True)

        await self._safe_move_item(docs_dir / "ALL_SPEC.md", phase_dir / "ALL_SPEC.md")
        await self._safe_move_item(
            docs_dir / "USER_TEST_SCENARIO.md", phase_dir / "USER_TEST_SCENARIO.md"
        )

        tutorials_dir = Path.cwd() / "tutorials"
        if tutorials_dir.exists():
            for item in tutorials_dir.iterdir():
                await self._safe_move_item(item, phase_dir / "tutorials" / item.name)
            await anyio.Path(tutorials_dir).mkdir(exist_ok=True)

        from src.config import settings

        templates_dir = settings.paths.templates
        if templates_dir.exists():
            for cycle_dir in sorted(
                [d for d in templates_dir.iterdir() if d.is_dir() and d.name.startswith("CYCLE")]
            ):
                await self._safe_move_item(cycle_dir, phase_dir / "templates" / cycle_dir.name)

    def _reset_project_state(self, phase_dir: Path) -> None:
        state_mgr = StateManager()
        if state_mgr.STATE_FILE.exists():
            shutil.copy2(str(state_mgr.STATE_FILE), str(phase_dir / "project_state.json"))
            state_mgr.STATE_FILE.unlink()
            console.print("Project state reset (project_state.json archived and removed).")

    def _prepare_next_phase(self, docs_dir: Path) -> None:
        (docs_dir / "ALL_SPEC.md").touch()
        (docs_dir / "USER_TEST_SCENARIO.md").touch()
        (docs_dir / "system_prompts").mkdir(exist_ok=True)

    async def _commit_archived_phase(self, next_phase_num: int) -> None:
        from src.config import settings

        msg = settings.ARCHIVE_COMMIT_MESSAGE.format(phase_num=next_phase_num)
        try:
            await self.git.add_all()
            await self.git.commit(msg)
        except Exception as e:
            from src.utils import logger

            logger.warning(f"Failed to commit archive: {e}")
