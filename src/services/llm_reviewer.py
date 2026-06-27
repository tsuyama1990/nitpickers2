import base64
import json
import subprocess
from pathlib import Path

import anyio
import litellm
from pydantic import ValidationError

from src.domain_models import AuditorReport, FixPlanSchema, UatExecutionState
from src.utils import extract_json_from_text, logger


class LLMReviewer:
    """
    Direct LLM Client for conducting static code reviews.
    Uses litellm to communicate with various LLM providers (OpenRouter, Gemini, etc.).
    """

    def __init__(self) -> None:

        import os

        # Enable LangSmith supervision natively through litellm if configured
        if os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true":
            if "langsmith" not in litellm.success_callback:
                litellm.success_callback.append("langsmith")
            if "langsmith" not in litellm.failure_callback:
                litellm.failure_callback.append("langsmith")

        # We rely on litellm's environment variable handling for API keys.
        # Ensure litellm is verbose enough for debugging if needed.
        # DO NOT set suppress_instrumentation = True as it can interfere with callbacks.

    @staticmethod
    async def _get_directory_tree(root_path: str | Path | None = None, max_depth: int = 3) -> str:
        """Generate a directory tree string for the project.

        Uses `tree` command if available, otherwise falls back to a simple pathlib walk.
        Only includes source directories (src/, tests/, tutorials/) up to max_depth.
        """
        root: Path = Path(root_path) if root_path else Path.cwd()
        include_prefixes = ("src/", "tests/", "tutorials/", "dev_documents/")

        try:
            result = await anyio.to_thread.run_sync(
                lambda: subprocess.run(
                    ["tree", str(root), "-L", str(max_depth),
                     "--charset", "utf-8", "-I", "__pycache__|*.pyc|.git|.venv|venv|*.egg-info|node_modules"],
                    capture_output=True, text=True, timeout=10,
                )
            )
            if result.returncode == 0:
                lines = result.stdout.splitlines()
                # Only keep lines matching include prefixes
                filtered = [lines[0]]  # top-level dir name
                for line in lines[1:]:
                    stripped = line.strip()
                    # Check if the path (after tree chars) matches our prefixes
                    path_part = stripped.lstrip("│├──└─ ")
                    if path_part.startswith(include_prefixes) or not path_part:
                        filtered.append(line)
                return "\n".join(filtered[:80])  # cap at 80 lines
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            pass

        # Fallback: simple pathlib walk
        lines = [f"{Path(root).name}/"]
        root_path = Path(root)
        for prefix in include_prefixes:
            p = root_path / prefix.rstrip("/")
            if p.exists():
                for child in sorted(p.rglob("*"))[:60]:
                    if child.is_dir():
                        lines.append(f"    {child.relative_to(root_path)}/")
                    else:
                        lines.append(f"    {child.relative_to(root_path)}")
        return "\n".join(lines)

    async def _validate_paths(
        self, target_files: dict[str, str], context_docs: dict[str, str]
    ) -> str | None:
        if not target_files:
            logger.warning("review_code called with empty target_files dictionary.")
            return "-> REVIEW_FAILED\n\n### Critical Issues\n- **Issue**: SYSTEM_ERROR: No target files provided for review.\n  - Location: `Unknown`\n  - Concrete Fix: Ensure files are modified before requesting an audit."

        import pathlib

        import anyio

        cwd = await anyio.Path(pathlib.Path.cwd()).resolve(strict=False)

        async def _is_path_safe(p: str) -> bool:
            if ".." in p:
                return False
            try:
                p_path = anyio.Path(p)
                resolved = await p_path.resolve(strict=False)
                return resolved.is_relative_to(cwd)
            except Exception as e:
                logger.debug(f"Path resolution failed for {p}: {e}")
                return False

        for path in list(target_files.keys()):
            if not await _is_path_safe(path):
                logger.warning(f"review_code rejecting invalid target file path: {path}")
                return f"-> REVIEW_FAILED\n\n### Critical Issues\n- **Issue**: SYSTEM_ERROR: Invalid target file path detected: {path}\n  - Location: `Unknown`\n  - Concrete Fix: Remove path traversal or unsafe characters."

        for path in list(context_docs.keys()):
            if not await _is_path_safe(path):
                logger.warning(f"review_code rejecting invalid context doc path: {path}")
                del context_docs[path]

        return None

    async def review_code(
        self,
        target_files: dict[str, str],
        context_docs: dict[str, str],
        instruction: str,
        model: str,
    ) -> str:
        """
        Sends file contents and instructions to the LLM for review.
        Uses response_format=AuditorReport for structured output (stable Pydantic compliance).
        Falls back to text-based JSON extraction if structured output is not supported by the model.
        Includes directory tree for structural context.
        """

        validation_error = await self._validate_paths(target_files, context_docs)
        if validation_error:
            return validation_error

        total_files = len(target_files) + len(context_docs)
        logger.info(
            f"LLMReviewer: preparing structured review for {total_files} files using model {model}"
        )

        # 1. Add directory tree for structural context
        directory_tree = await self._get_directory_tree()
        logger.info(f"LLMReviewer: directory tree generated ({len(directory_tree)} chars)")

        # 2. Build prompt with directory tree + context/target separation
        prompt = self._construct_prompt(
            target_files, context_docs, instruction, directory_tree=directory_tree
        )

        # Estimate tokens (approx 4 chars per token)
        estimated_tokens = len(prompt) // 4
        logger.info(f"LLMReviewer: estimated payload size: {estimated_tokens} tokens")

        if estimated_tokens > 100000:
            logger.warning(
                f"LLMReviewer: extremely large payload detected ({estimated_tokens} tokens)."
            )

        # 3. Schema injection as fallback text hint
        schema_prompt = json.dumps(AuditorReport.model_json_schema(), indent=2)

        # 4. Retry logic with structured output first, then fallback
        for attempt in range(3):
            try:
                if attempt > 0:
                    delay = (2**attempt) * 5  # 10s, 20s
                    logger.info(f"Retrying LLM review in {delay}s...")
                    await anyio.sleep(delay)

                # Try structured output (response_format) first
                # This forces the model to output valid JSON matching AuditorReport schema
                kwargs: dict[str, object] = {
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are an automated code reviewer. You must strictly follow the "
                                "provided instructions and only review the target code. "
                                "You MUST return valid JSON matching the required schema. "
                                "IMPORTANT: Provide a comprehensive review. Report at least 3 critical issues if they exist, "
                                "but limit your 'issues' array to a MAXIMUM of 10 issues. "
                                f"The expected JSON schema is:\n```json\n{schema_prompt}\n```\n"
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 16384,
                }

                # litellm supports response_format with Pydantic model for compatible models
                # Not all models support structured output, so we try and fallback
                try:
                    kwargs["response_format"] = AuditorReport
                    response = await litellm.acompletion(**kwargs)
                except Exception:
                    # Model doesn't support structured output → fallback to text mode
                    logger.info(
                        f"LLMReviewer: structured output not supported by {model}, "
                        "falling back to text-based JSON extraction."
                    )
                    del kwargs["response_format"]
                    response = await litellm.acompletion(**kwargs)

                content_str = response.choices[0].message.content
                if content_str is None:
                    logger.warning(f"LLMReviewer: received empty content (None) for model {model}")
                    if attempt == 2:
                        return (
                            "-> REVIEW_FAILED\n\n### Critical Issues\n- **Issue**: SYSTEM_ERROR: "
                            f"LLM API returned empty content. \n  - Location: `Unknown` (Response: {response})"
                            "\n  - Concrete Fix: Check if the model is available and supports the request."
                        )
                    continue

                # Parse with Pydantic validation (works for both structured and fallback modes)
                try:
                    report = AuditorReport.model_validate_json(content_str)
                except ValidationError:
                    # Structured output may still fail; try text extraction
                    clean_json = extract_json_from_text(content_str)
                    report = AuditorReport.model_validate_json(clean_json)

                return self._format_as_markdown(report)

            except (ValidationError, Exception) as e:
                logger.warning(f"LLMReviewer attempt {attempt + 1} failed: {e}")
                if attempt == 2:
                    logger.error(f"LLMReviewer failed completely after 3 attempts. Last error: {e}")
                    return (
                        "-> REVIEW_FAILED\n\n### Critical Issues\n- **Issue**: SYSTEM_ERROR: "
                        f"LLM API generated invalid JSON or failed. ({e})\n  - Location: `Unknown`\n"
                        "  - Concrete Fix: Ensure your changes are simple and try again."
                    )

        return (
            "-> REVIEW_FAILED\n\n### Critical Issues\n- **Issue**: SYSTEM_ERROR: "
            "Review loop failed unexpectedly\n  - Location: `Unknown`\n"
            "  - Concrete Fix: Ensure your changes are simple and try again."
        )

    async def diagnose_uat_failure(
        self,
        uat_state: UatExecutionState,
        instruction: str,
        model: str,
    ) -> FixPlanSchema:
        """
        Stateless diagnostic outer loop. Analyzes UAT execution logs and Multi-Modal artifacts
        to provide a highly specific FixPlanSchema.
        """
        logger.info(f"LLMReviewer: starting UAT failure diagnosis using {model}")

        from src.utils import sanitize_for_llm

        # Robust sanitization to prevent prompt injection and handle API payloads securely
        safe_stdout = sanitize_for_llm(uat_state.stdout)
        safe_stderr = sanitize_for_llm(uat_state.stderr)
        safe_instruction = sanitize_for_llm(instruction)

        content_parts: list[dict[str, str | dict[str, str]]] = [
            {
                "type": "text",
                "text": f"{safe_instruction}\n\n# Execution Output\n\nExit Code: {uat_state.exit_code}\n\n## Stdout\n```\n{safe_stdout}\n```\n\n## Stderr\n```\n{safe_stderr}\n```\n",
            }
        ]

        # Attach multimodal artifacts
        for artifact in uat_state.artifacts:
            try:
                img_path = anyio.Path(artifact.screenshot_path)
                if await img_path.exists():
                    img_data = await img_path.read_bytes()
                    encoded = base64.b64encode(img_data).decode("utf-8")
                    content_parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        }
                    )
                    safe_traceback = sanitize_for_llm(artifact.traceback)
                    content_parts.append(
                        {
                            "type": "text",
                            "text": f"\n# Traceback for artifact {artifact.test_id}\n```\n{safe_traceback}\n```\n",
                        }
                    )
            except Exception as e:
                logger.warning(f"Failed to process multimodal artifact {artifact.test_id}: {e}")

        for attempt in range(3):
            try:
                if attempt > 0:
                    delay = (2**attempt) * 5
                    logger.info(f"Retrying UAT failure diagnosis in {delay}s...")
                    await anyio.sleep(delay)

                response = await litellm.acompletion(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are the Outer Loop Diagnostician. You must strictly output valid JSON matching the FixPlanSchema.",
                        },
                        {"role": "user", "content": content_parts},
                    ],
                    response_format=FixPlanSchema,
                    temperature=0.0,
                    max_tokens=8192,
                )

                content_str = response.choices[0].message.content
                if content_str is None:
                    logger.warning(
                        f"diagnose_uat_failure: received empty content (None) for model {model}"
                    )
                    if attempt == 2:
                        from src.domain_models import FilePatchEntry as FixFilePatch

                        return FixPlanSchema(
                            defect_description=f"SYSTEM_ERROR: LLM API returned empty content for model {model}.",
                            patches=[
                                FixFilePatch(
                                    target_file="Unknown",
                                    git_diff_patch="Please check model availability.",
                                )
                            ],
                        )
                    continue

                clean_json = extract_json_from_text(content_str)
                return FixPlanSchema.model_validate_json(clean_json)
            except (ValidationError, Exception) as e:
                logger.warning(f"diagnose_uat_failure attempt {attempt + 1} failed: {e}")
                if attempt == 2:
                    logger.error(
                        f"diagnose_uat_failure failed completely after 3 attempts. Last error: {e}"
                    )
                    from src.domain_models import FilePatchEntry as FixFilePatch

                    return FixPlanSchema(
                        defect_description=f"SYSTEM_ERROR: LLM API generated invalid JSON or failed. {e}",
                        patches=[
                            FixFilePatch(
                                target_file="Unknown",
                                git_diff_patch="Please review the UAT logs manually and provide a fix.",
                            )
                        ],
                    )

        # Unreachable but mypy needs it
        from src.domain_models import FilePatchEntry as FixFilePatch

        return FixPlanSchema(
            defect_description="SYSTEM_ERROR: Review loop failed unexpectedly.",
            patches=[
                FixFilePatch(
                    target_file="Unknown", git_diff_patch="Please review the UAT logs manually."
                )
            ],
        )

    def _format_as_markdown(self, report: AuditorReport) -> str:
        """Converts the deeply nested AuditorReport Pydantic object into a clean Markdown string for the Coder."""
        feedback = "-> REVIEW_PASSED\n\n" if report.is_passed else "-> REVIEW_FAILED\n\n"

        feedback += f"### Summary\n{report.summary}\n\n"

        if report.issues:
            feedback += "### Critical Issues\n"
            fatal_count = sum(1 for i in report.issues if i.severity == "fatal")
            warning_count = sum(1 for i in report.issues if i.severity == "warning")
            feedback += f"**{fatal_count} FATAL, {warning_count} WARNING**\n\n"

            for issue in report.issues:
                severity_badge = "🔴" if issue.severity == "fatal" else "🟡"
                feedback += (
                    f"- {severity_badge} **[{issue.category.upper()}][{issue.severity.upper()}]**: "
                    f"{issue.issue_description}\n"
                )
                feedback += f"  - **Location**: `{issue.file_path}`\n"
                feedback += (
                    f"  - **Target Snippet**:\n    ```\n    {issue.target_code_snippet}\n    ```\n"
                )
                feedback += f"  - **Concrete Fix**: {issue.concrete_fix}\n\n"

        return feedback

    def _construct_prompt(
        self,
        target_files: dict[str, str],
        context_docs: dict[str, str],
        instruction: str,
        directory_tree: str = "",
    ) -> str:
        """
        Format the prompt with strict Context/Target separation.
        Includes directory tree for structural context.
        """

        # 0. Directory Tree (structural context)
        tree_section = ""
        if directory_tree:
            tree_section = f"""
###################

📁 PROJECT STRUCTURE

```
{directory_tree}
```
"""

        # 1. Context Section (Specs)
        context_section = ""
        for name, content in context_docs.items():
            context_section += f"\nFile: {name} (READ-ONLY SPECIFICATION)\n```\n{content}\n```\n"

        # 2. Target Section (Code)
        target_section = ""
        for name, content in target_files.items():
            # Detect git diff format from content, not filename
            if content.startswith(("diff --git", "--- a/")):
                lang = "diff"
            elif name.endswith(".py"):
                lang = "python"
            else:
                lang = ""
            target_section += f"\nFile: {name} (AUDIT TARGET)\n```{lang}\n{content}\n```\n"

        # 3. Assemble Prompt
        return f"""
{instruction}
{tree_section}
###################

🚫 READ-ONLY CONTEXT (GROUND TRUTH)

The following files define the specifications.
You must NOT critique, review, or suggest changes to these files.
Use them ONLY as the reference to judge the code.

###################
{context_section}

###################

🎯 AUDIT TARGET (CODE TO REVIEW)

Strictly review the following files against the context above.
Provide feedback ONLY for these files.

###################
{target_section}
"""
