import json
import os
import re
import sys
from pathlib import Path

from rich.console import Console

from src.config import settings
from src.domain_models.observability_config import ObservabilityConfig
from src.utils import logger

console = Console()


class EnvironmentValidator:
    def verify(self) -> None:
        """
        Validates observability parameters and checks explicitly/implicitly required
        dependencies based on environment variables and the local configuration.
        Implements the Phase 0 Gatekeeper pattern.
        """
        console.rule("[bold red]Phase 0: Environment & Observability Verification[/bold red]")
        self._verify_observability()
        self._verify_required_keys()
        self._scan_implicit_dependencies()
        self._verify_dynamic_requirements()
        self._ensure_gitignore()
        console.print("[green]Environment & Observability verified successfully.[/green]")

    def _verify_observability(self) -> None:
        try:
            # Pydantic schema enforcing invariants
            ObservabilityConfig(
                langchain_tracing_v2=os.getenv("LANGCHAIN_TRACING_V2", ""),
                langchain_api_key=os.getenv("LANGCHAIN_API_KEY", ""),
                langchain_project=os.getenv("LANGCHAIN_PROJECT", ""),
            )
        except Exception as e:
            console.print("[bold red]Observability check failed![/bold red]")
            console.print(f"[red]{e!s}[/red]")
            console.print(
                "[yellow]Please configure LANGCHAIN_TRACING_V2=true, LANGCHAIN_API_KEY, "
                "and LANGCHAIN_PROJECT in your .env file.[/yellow]"
            )
            sys.exit(1)

    def _verify_required_keys(self) -> None:
        required_keys = ["OPENROUTER_API_KEY", "JULES_API_KEY", "E2B_API_KEY"]
        for key in required_keys:
            if not os.getenv(key):
                console.print(f"[bold red]Missing required API key: {key}[/bold red]")
                console.print(
                    "[yellow]Please configure all required API keys in your .env file.[/yellow]"
                )
                sys.exit(1)

    def _scan_implicit_dependencies(self) -> None:
        # Implicit dependency scan via SPEC documents
        try:
            docs_dir = settings.paths.documents_dir
            if not docs_dir.exists():
                docs_dir = Path.cwd() / "dev_documents"

            spec_path = docs_dir / "system_prompts" / "SPEC.md"
            if spec_path.exists():
                content = spec_path.read_text(encoding="utf-8")
                # Very basic scan for implicitly required secrets like DATABASE_URL, OPENAI_API_KEY
                for secret in settings.known_implicit_secrets:
                    if re.search(
                        r"\b" + re.escape(secret) + r"\b", content, re.IGNORECASE
                    ) and not os.getenv(secret):
                        console.print(
                            "[bold red]Implicit Dependency Missing: A required secret is missing.[/bold red]"
                        )
                        console.print(
                            "[yellow]The specification file references a known secret, "
                            "but it was not found in the environment. Please configure required secrets.[/yellow]"
                        )
                        sys.exit(1)
        except SystemExit:
            raise
        except Exception as e:
            logger.warning(f"Error scanning SPEC.md for implicit dependencies: {e}")

    def _verify_dynamic_requirements(self) -> None:
        # Directive A: Pre-Flight Check based on required_envs.json
        try:
            docs_dir = settings.paths.documents_dir
            if not docs_dir.exists():
                docs_dir = Path.cwd() / "dev_documents"

            required_envs_path = docs_dir / "required_envs.json"
            if required_envs_path.exists():
                try:
                    required_envs = json.loads(required_envs_path.read_text(encoding="utf-8"))
                    if isinstance(required_envs, list):
                        missing_keys = [key for key in required_envs if not os.getenv(key)]
                        if missing_keys:
                            console.print(
                                "\n[bold red][ERROR] Missing dynamically required API keys![/bold red]"
                            )
                            console.print(
                                "[red]The system architecture explicitly requires the following keys to proceed:[/red]"
                            )
                            for key in missing_keys:
                                console.print(f"  - [bold yellow]{key}[/bold yellow]")
                            console.print(
                                "\n[yellow]Please add these keys to your local .env file or environment variables "
                                "and re-run the command.[/yellow]"
                            )
                            sys.exit(1)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse required_envs.json: {e}")
        except SystemExit:
            raise
        except Exception as e:
            logger.warning(f"Error checking required_envs.json: {e}")

    def _ensure_gitignore(self) -> None:
        """Ensures that logs and worktrees are ignored by git and untracked from the index."""
        gitignore = Path.cwd() / ".gitignore"
        required_ignores = ["logs/", ".nitpick/worktrees/"]
        try:
            if gitignore.exists():
                content = gitignore.read_text(encoding="utf-8")
                missing = [line for line in required_ignores if line not in content]
                if missing:
                    with gitignore.open("a", encoding="utf-8") as f:
                        if content and not content.endswith("\n"):
                            f.write("\n")
                        for line in missing:
                            f.write(f"{line}\n")
                    logger.info(f"Updated .gitignore with: {', '.join(missing)}")
            else:
                gitignore.write_text("\n".join(required_ignores) + "\n", encoding="utf-8")
                logger.info("Created .gitignore with default Nitpick excludes.")
        except Exception as e:
            logger.warning(f"Failed to verify/update .gitignore: {e}")

        # CRITICAL: Untrack any previously-committed files under ephemeral directories.
        # .gitignore alone does NOT protect already-tracked files — they stay in the index
        # and their modifications cause `git checkout` to fail when switching branches.
        import subprocess
        untrack_dirs = ["logs", ".nitpick"]
        for d in untrack_dirs:
            dir_path = Path.cwd() / d
            try:
                # Force removal from index if tracked
                subprocess.run(
                    ["git", "rm", "-r", "--cached", "--ignore-unmatch", d],
                    capture_output=True,
                    cwd=str(Path.cwd()),
                )
                logger.info(f"Untracked '{d}' from Git index to prevent checkout conflicts.")
            except Exception as e:
                logger.warning(f"Failed to untrack '{d}/' from Git index: {e}")
