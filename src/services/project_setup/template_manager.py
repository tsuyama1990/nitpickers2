import shutil
from pathlib import Path

from src.config import settings
from src.utils import logger


class TemplateManager:
    """Manages project templates and specifications."""

    def setup_templates(
        self, templates_path: str
    ) -> tuple[Path, Path, Path, Path, Path, Path, Path, Path]:
        docs_dir = Path(settings.paths.documents_dir)
        docs_dir.mkdir(parents=True, exist_ok=True)

        templates_dest = Path(templates_path)
        templates_dest.mkdir(parents=True, exist_ok=True)

        self._create_all_spec(docs_dir)
        self._create_user_test_scenario(docs_dir)
        req_envs_path = self._create_required_envs(docs_dir)

        (docs_dir / "contracts").mkdir(exist_ok=True)
        system_prompts_dir = docs_dir / "system_prompts"
        system_prompts_dir.mkdir(exist_ok=True)

        self.copy_default_templates(system_prompts_dir)
        env_example_path = self._create_env_example()
        root_env_path = self._create_root_env()
        gitignore_path = self._update_gitignore()
        github_dir = self._create_github_workflow()

        src_dir = Path.cwd() / "src"
        src_dir.mkdir(parents=True, exist_ok=True)

        tests_dir = Path.cwd() / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)

        return (
            docs_dir,
            env_example_path,
            gitignore_path,
            github_dir,
            src_dir,
            tests_dir,
            root_env_path,
            req_envs_path,
        )

    def _create_all_spec(self, docs_dir: Path) -> None:
        all_spec_dest = docs_dir / "ALL_SPEC.md"
        if not all_spec_dest.exists():
            all_spec_dest.write_text(
                "# Project Specifications\n\nDefine your project requirements here.",
                encoding="utf-8",
            )

    def _create_user_test_scenario(self, docs_dir: Path) -> None:
        uts_dest = docs_dir / "USER_TEST_SCENARIO.md"
        if uts_dest.exists() and uts_dest.is_dir():
            logger.warning(f"Removing directory {uts_dest} to replace with file")
            shutil.rmtree(uts_dest)

        if not uts_dest.exists():
            uts_content = """# User Test Scenario & Tutorial Plan

## Aha! Moment
Describe the "Magic Moment" where the user first realizes the value of this software.
(e.g., "The user runs one command and sees a beautiful report generated instantly.")

## Prerequisites
List what the user needs before running the tutorial.
(e.g., "OpenAI API Key", "Docker installed")

## Success Criteria
What defines a successful user experience?
(e.g., "The tutorial runs from start to finish without errors in under 5 minutes.")
"""
            uts_dest.write_text(uts_content, encoding="utf-8")
            logger.info(f"✓ Created {uts_dest}")

    def copy_default_templates(self, system_prompts_dir: Path) -> None:
        # Use absolute path to src package templates
        import src

        source_dir = Path(src.__file__).parent / "templates"

        if not source_dir.exists():
            logger.warning(f"Template source directory not found: {source_dir}")
            return

        template_files = [f.name for f in source_dir.glob("*.md")]

        for template_file in template_files:
            source_file = source_dir / template_file
            dest_file = system_prompts_dir / template_file

            if source_file.exists() and not dest_file.exists():
                try:
                    shutil.copy(source_file, dest_file)
                    logger.info(f"✓ Created {template_file}")
                except Exception as e:
                    logger.warning(f"Failed to copy {template_file}: {e}")
            elif dest_file.exists():
                logger.debug(f"Skipping {template_file} (already exists)")

    def _create_root_env(self) -> Path:
        env_path = Path.cwd() / ".env"
        if not env_path.exists():
            env_path.write_text("", encoding="utf-8")
            logger.info(f"✓ Created blank .env file at {env_path}")
        return env_path

    def _create_required_envs(self, docs_dir: Path) -> Path:
        req_envs_path = docs_dir / "required_envs.json"
        if not req_envs_path.exists():
            req_envs_path.write_text("[]", encoding="utf-8")
            logger.info(f"✓ Created {req_envs_path}")
        return req_envs_path

    def _create_env_example(self) -> Path:
        env_example_path = Path.cwd() / ".nitpick" / ".env.example"
        env_example_path.parent.mkdir(exist_ok=True)

        if not env_example_path.exists():
            env_example_content = """# ==========================================
# NITPICKERS Agent Execution Secrets
# ==========================================
# Copy this file to .nitpick/.env and fill in your actual API keys

# Jules API Key (Required for AI-powered development agent)
# Get your key from: https://jules.googleapis.com/
JULES_API_KEY=your-jules-api-key-here

# E2B API Key (Required for sandbox execution)
# Get your key from: https://e2b.dev/
E2B_API_KEY=your-e2b-api-key-here

# OpenRouter API Key (Required if using OpenRouter models)
# Get your key from: https://openrouter.ai/
OPENROUTER_API_KEY=your-openrouter-api-key-here

# ============================================================================
# Model Configuration (Simplified)
# ============================================================================
# You only need to set SMART_MODEL and FAST_MODEL.
# These will be used for all agents (Auditor, QA Analyst, Reviewer, etc.)

# SMART_MODEL: Used for complex tasks like code editing and architecture
# Examples:
#   - OpenRouter: openrouter/meta-llama/llama-3.3-70b-instruct:free
#   - Anthropic: claude-3-5-sonnet
#   - Gemini: gemini-2.0-flash-exp
SMART_MODEL=openrouter/meta-llama/llama-3.3-70b-instruct:free

# FAST_MODEL: Used for reading, auditing, and analysis tasks
# Examples:
#   - OpenRouter: openrouter/nousresearch/hermes-3-llama-3.1-405b:free
#   - Anthropic: claude-3-5-sonnet
#   - Gemini: gemini-2.0-flash-exp
FAST_MODEL=openrouter/nousresearch/hermes-3-llama-3.1-405b:free

# ============================================================================
# Observability (LangSmith)
# ============================================================================
LANGCHAIN_TRACING_V2=true
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=nitpick-dev

# ============================================================================
# Optional: Advanced Configuration
# ============================================================================
# Uncomment and modify these if you need fine-grained control

# Override specific agent models (optional)
# AC_CDD_AGENTS__AUDITOR_MODEL=openrouter/meta-llama/llama-3.3-70b-instruct:free
# AC_CDD_AGENTS__QA_ANALYST_MODEL=openrouter/nousresearch/hermes-3-llama-3.1-405b:free

# Override reviewer models (optional)
# AC_CDD_REVIEWER__SMART_MODEL=openrouter/meta-llama/llama-3.3-70b-instruct:free
# AC_CDD_REVIEWER__FAST_MODEL=openrouter/nousresearch/hermes-3-llama-3.1-405b:free

# ============================================================================
# Notes
# ============================================================================
# 1. After copying this to .nitpick/.env, it will be automatically loaded
# 2. Never commit your actual API keys to version control
# 3. The .nitpick/.env file is already in .gitignore
"""
            env_example_path.write_text(env_example_content, encoding="utf-8")
            logger.info(f"✓ Created .env.example at {env_example_path}")
            logger.info("  Please copy it to .nitpick/.env and fill in your API keys:")
            logger.info(f"  cp {env_example_path} .nitpick/.env")
        return env_example_path

    def _update_gitignore(self) -> Path:
        gitignore_path = Path.cwd() / ".gitignore"
        gitignore_entries = [
            "# AC-CDD Configuration",
            ".env",
            ".nitpick/",  # Ignore entire state directory
            ".nitpick/project_state_local.json",
            "dev_documents/project_state.json",
            "dev_documents/project_state_local.json",
        ]

        if gitignore_path.exists():
            content = gitignore_path.read_text(encoding="utf-8")
            entries_to_add = [entry for entry in gitignore_entries if entry not in content]
            if entries_to_add:
                with gitignore_path.open("a", encoding="utf-8") as f:
                    f.write("\n" + "\n".join(entries_to_add) + "\n")
                logger.info("✓ Updated .gitignore")
        else:
            gitignore_path.write_text("\n".join(gitignore_entries) + "\n", encoding="utf-8")
            logger.info("✓ Created .gitignore")
        return gitignore_path

    def _create_github_workflow(self) -> Path:
        github_dir = Path.cwd() / ".github"
        workflows_dir = github_dir / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)

        ci_yml_path = workflows_dir / "ci.yml"
        if not ci_yml_path.exists():
            ci_content = """name: CI

on:
  push:
    branches: [ main, master, "dev/**", "feature/**" ]
  pull_request:
    branches: [ main, master, "dev/**", "feature/**" ]

jobs:
  quality:
    name: Code Quality
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          version: "latest"

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install Dependencies
        run: uv sync --all-extras --dev

      - name: Lint & Format (Ruff)
        run: |
          uv run ruff check .
          uv run ruff format --check .

      - name: Type Check (Mypy)
        run: uv run mypy .

      - name: Run Unit Tests
        run: uv run pytest tests/unit/

      - name: Install Playwright Browsers
        env:
          DEBIAN_FRONTEND: noninteractive
        run: uv run playwright install --with-deps chromium

      - name: Run E2E Tests
        env:
          JULES_API_KEY: ${{ secrets.JULES_API_KEY }}
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
        run: |
          if [ -d "tests/e2e" ] && [ "$(ls -A tests/e2e)" ]; then
            if [ -z "$JULES_API_KEY" ] && [ -z "$OPENROUTER_API_KEY" ]; then
              echo "Skipping E2E tests: API keys not configured in GitHub Secrets."
            else
              uv run pytest tests/e2e/
            fi
          else
            echo "No E2E tests found, skipping."
          fi
"""
            ci_yml_path.write_text(ci_content, encoding="utf-8")
            logger.info(f"✓ Created CI workflow at {ci_yml_path}")
        return github_dir
