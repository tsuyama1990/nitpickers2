import base64

import anyio
import litellm
from pydantic import ValidationError

from src.domain_models import AuditorReport, FixPlanSchema, UatExecutionState
from src.utils import logger
from src.utils_json import extract_json_from_text


class LLMReviewer:
    """
    Direct LLM Client for conducting static code reviews.
    Uses litellm to communicate with various LLM providers (OpenRouter, Gemini, etc.).
    """

    def __init__(self, sandbox_runner: object | None = None) -> None:
        # sandbox_runner is accepted for dependency injection compatibility
        # even if not strictly used by this class (files are passed as content)
        self.sandbox = sandbox_runner

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
        Validates the output strictly against the AuditorReport Pydantic schema.
        """

        validation_error = await self._validate_paths(target_files, context_docs)
        if validation_error:
            return validation_error

        total_files = len(target_files) + len(context_docs)
        logger.info(
            f"LLMReviewer: preparing structured review for {total_files} files using model {model}"
        )

        # specific prompt construction with strict separation
        prompt = self._construct_prompt(target_files, context_docs, instruction)

        # Estimate tokens (approx 4 chars per token)
        estimated_tokens = len(prompt) // 4
        logger.info(f"LLMReviewer: estimated payload size: {estimated_tokens} tokens")

        if estimated_tokens > 100000:  # Threshold for most common models
            logger.warning(
                f"LLMReviewer: extremely large payload detected ({estimated_tokens} tokens). This may cause timeouts or 500 errors on some providers."
            )

        # Schema injection to guarantee correct JSON structure
        import json

        schema_prompt = json.dumps(AuditorReport.model_json_schema(), indent=2)

        # Retry logic (up to 2 retries, total 3 attempts)
        for attempt in range(3):
            try:
                if attempt > 0:
                    delay = (2**attempt) * 5  # 10s, 20s
                    logger.info(f"Retrying LLM review in {delay}s...")
                    await anyio.sleep(delay)

                response = await litellm.acompletion(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are an automated code reviewer. You must strictly follow the "
                                "provided instructions and only review the target code. You MUST return valid JSON. "
                                f"Your response MUST exactly match the following JSON schema:\n```json\n{schema_prompt}\n```\n"
                                "IMPORTANT: Provide a comprehensive review. Report at least 3 critical issues if they exist, "
                                "but limit your 'issues' array to a MAXIMUM of 10 issues to prevent context overflow. "
                                "Keep your 'thought' field EXTREMELY concise (max 2 sentences) to ensure the JSON structure is completed within the token limit."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,  # Deterministic output for reviews
                    max_tokens=16384,  # Increased to prevent JSON truncation on large reviews
                )

                content_str = response.choices[0].message.content
                if content_str is None:
                    # Specific log for missing content without raising but triggering retry or fallback
                    logger.warning(f"LLMReviewer: received empty content (None) for model {model}")
                    logger.debug(f"DEBUG Response Object: {response}")

                    if attempt == 2:
                        return f"-> REVIEW_FAILED\n\n### Critical Issues\n- **Issue**: SYSTEM_ERROR: LLM API returned empty content. \n  - Location: `Unknown` (Response: {response})\n  - Concrete Fix: Check if the model {model} is available and supports the request."
                    continue  # Try again

                # Parse the response safely into our robust Pydantic model
                clean_json = extract_json_from_text(content_str)
                report = AuditorReport.model_validate_json(clean_json)
                return self._format_as_markdown(report)

            except (ValidationError, Exception) as e:
                logger.warning(f"LLMReviewer attempt {attempt + 1} failed: {e}")
                if attempt == 2:
                    logger.error(f"LLMReviewer failed completely after 3 attempts. Last error: {e}")
                    return f"-> REVIEW_FAILED\n\n### Critical Issues\n- **Issue**: SYSTEM_ERROR: LLM API generated invalid JSON or failed. ({e})\n  - Location: `Unknown` (Line Unknown)\n  - Concrete Fix: Ensure your changes are simple and try again."

        return "-> REVIEW_FAILED\n\n### Critical Issues\n- **Issue**: SYSTEM_ERROR: Review loop failed unexpectedly\n  - Location: `Unknown`\n  - Concrete Fix: Ensure your changes are simple and try again."

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

        from src.utils_sanitization import sanitize_for_llm

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
                        from src.domain_models.fix_plan_schema import FilePatch

                        return FixPlanSchema(
                            defect_description=f"SYSTEM_ERROR: LLM API returned empty content for model {model}.",
                            patches=[
                                FilePatch(
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
                    from src.domain_models.fix_plan_schema import FilePatch

                    # Fallback schema to not break the pipeline entirely, though we ideally raise
                    return FixPlanSchema(
                        defect_description=f"SYSTEM_ERROR: LLM API generated invalid JSON or failed. {e}",
                        patches=[
                            FilePatch(
                                target_file="Unknown",
                                git_diff_patch="Please review the UAT logs manually and provide a fix.",
                            )
                        ],
                    )

        # Unreachable but mypy needs it
        from src.domain_models.fix_plan_schema import FilePatch

        return FixPlanSchema(
            defect_description="SYSTEM_ERROR: Review loop failed unexpectedly.",
            patches=[
                FilePatch(
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
            for issue in report.issues:
                feedback += f"- **[{issue.category.upper()}]**: {issue.issue_description}\n"
                feedback += f"  - **Location**: `{issue.file_path}`\n"
                feedback += (
                    f"  - **Target Snippet**:\n    ```\n    {issue.target_code_snippet}\n    ```\n"
                )
                feedback += f"  - **Concrete Fix**: {issue.concrete_fix}\n\n"

        return feedback

    def _construct_prompt(
        self, target_files: dict[str, str], context_docs: dict[str, str], instruction: str
    ) -> str:
        """
        Format the prompt with strict Context/Target separation.
        """

        # 1. Context Section (Specs)
        context_section = ""
        for name, content in context_docs.items():
            context_section += f"\nFile: {name} (READ-ONLY SPECIFICATION)\n```\n{content}\n```\n"

        # 2. Target Section (Code)
        target_section = ""
        for name, content in target_files.items():
            # Add python hint for .py files
            lang = "python" if name.endswith(".py") else ""
            target_section += f"\nFile: {name} (AUDIT TARGET)\n```{lang}\n{content}\n```\n"

        # 3. Assemble Prompt
        return f"""
{instruction}

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
