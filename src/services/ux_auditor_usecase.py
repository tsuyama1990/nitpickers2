import base64
from pathlib import Path
from typing import Any

import anyio
import litellm
from pydantic import ValidationError
from rich.console import Console

from src.config import settings
from src.domain_models import UXAuditReport
from src.state import CycleState
from src.utils import logger

console = Console()


class UxAuditorUseCase:
    """
    Executes a Heuristic UX Audit using the UAT screenshots (multimodal) and a system prompt.
    Returns the UXAuditReport which will be appended to the state, and never fails the pipeline.
    """

    def __init__(self) -> None:
        pass

    async def execute(self, state: CycleState) -> dict[str, Any]:
        console.print("\n[bold cyan]Starting UX/UI Audit...[/bold cyan]")

        # 1. Gracefully handle the absence of UAT Execution State or Artifacts
        if not state.uat.uat_execution_state or not state.uat.uat_execution_state.artifacts:
            console.print(
                "[dim]No UAT execution state or artifacts found. Skipping UX Audit.[/dim]"
            )
            return {"uat": {"ux_audit_report": self._empty_report()}}

        screenshots = [
            a.screenshot_path for a in state.uat.uat_execution_state.artifacts if a.screenshot_path
        ]

        if not screenshots:
            console.print("[dim]No screenshots found in artifacts. Skipping UX Audit.[/dim]")
            return {"uat": {"ux_audit_report": self._empty_report()}}

        instruction_text = settings.read_template("UX_AUDITOR_INSTRUCTION.md")
        if not instruction_text:
            logger.warning("UX Auditor instructions missing, skipping UX Audit.")
            return {"uat": {"ux_audit_report": self._empty_report()}}

        # 2. Prepare the LLM Context
        content_parts: list[dict[str, Any]] = await self._prepare_content_parts(
            instruction_text, screenshots
        )

        if len(content_parts) == 2:  # Only text instructions remain
            console.print("[dim]Could not load any valid screenshots. Skipping UX Audit.[/dim]")
            return {"uat": {"ux_audit_report": self._empty_report()}}

        # 4. Invoke LLM
        console.print("[dim]Analyzing screenshots via Multi-Modal LLM...[/dim]")

        # We need an openrouter vision capable model, or fallback to default
        model = settings.reviewer.smart_model
        return await self._invoke_llm(content_parts, model)

    async def _prepare_content_parts(
        self, instruction_text: str, screenshots: list[str]
    ) -> list[dict[str, Any]]:
        content_parts: list[dict[str, Any]] = [
            {"type": "text", "text": instruction_text},
            {
                "type": "text",
                "text": "\nPlease evaluate the following UI screenshots according to the provided criteria. Only return valid JSON.",
            },
        ]

        for screenshot_path in screenshots:
            path = anyio.Path(screenshot_path)
            if not await path.exists():
                logger.warning(f"Screenshot missing on filesystem: {screenshot_path}")
                continue

            try:
                async with await path.open("rb") as f:
                    data = await f.read()
                    encoded = base64.b64encode(data).decode("utf-8")
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{encoded}"},
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to read/encode screenshot {screenshot_path}: {e}")

        return content_parts

    async def _invoke_llm(self, content_parts: list[dict[str, Any]], model: str) -> dict[str, Any]:
        for attempt in range(3):
            try:
                response = await litellm.acompletion(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a specialized UX Auditor. You must strictly output valid JSON matching the UXAuditReport schema.",
                        },
                        {"role": "user", "content": content_parts},
                    ],
                    response_format=UXAuditReport,
                    temperature=0.0,
                    max_tokens=4096,
                )

                content_str = response.choices[0].message.content
                if content_str:
                    report = UXAuditReport.model_validate_json(content_str)

                    self._save_report(report)
                    console.print(
                        f"[bold green]UX Audit Complete! Score: {report.overall_score}/100[/bold green]"
                    )

                    # Return partial state update
                    return {"uat": {"ux_audit_report": report}}

            except (ValidationError, Exception) as e:
                logger.warning(f"UxAuditorUseCase attempt {attempt + 1} failed: {e}")

        logger.error("UxAuditorUseCase failed completely after 3 attempts.")
        return {"uat": {"ux_audit_report": self._empty_report()}}

    def _empty_report(self) -> UXAuditReport:
        return UXAuditReport(overall_score=0, good_points=[], violations=[])

    def _save_report(self, report: UXAuditReport) -> None:
        """Saves the UX report to a local markdown artifact for humans to review."""
        report_path = Path("UX_REPORT.md")
        try:
            with report_path.open("w", encoding="utf-8") as f:
                f.write("# UX Audit Report\n\n")
                f.write(f"**Overall Score:** {report.overall_score}/100\n\n")

                if report.good_points:
                    f.write("## Good Points\n")
                    for point in report.good_points:
                        f.write(f"- {point}\n")
                    f.write("\n")

                if report.violations:
                    f.write("## Violations & Suggestions\n")
                    for v in report.violations:
                        f.write(f"- **[{v.principle}] {v.element}**\n")
                        f.write(f"  - *Issue:* {v.issue}\n")
                        f.write(f"  - *Suggestion:* {v.suggestion}\n\n")

            console.print(f"[dim]Saved UX Audit Report to {report_path.absolute()}[/dim]")
        except Exception as e:
            logger.warning(f"Failed to save UX_REPORT.md: {e}")
