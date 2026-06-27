"""Plan validation using litellm + structured output."""

from typing import Any

import litellm

from src.config import settings
from src.domain_models import PlanAuditResult


class PlanAuditor:
    """
    Validates implementation plans against requirements using an AI agent.
    Uses litellm for LLM calls with structured JSON output.
    """

    def __init__(self) -> None:
        self.model = self._resolve_model(settings.agents.auditor_model)
        self.system_prompt = settings.read_template(
            "PLAN_AUDITOR_SYSTEM.md",
            default="You are an expert Software Architect and QA Auditor.",
        )

    @staticmethod
    def _resolve_model(model_str: str) -> str:
        """Resolve model string to a litellm-compatible model name."""
        # openrouter/ prefix is passed as-is, litellm handles it
        return model_str

    async def audit_plan(
        self,
        plan_details: dict[str, Any],
        context_files: dict[str, str],
        phase: str = "coder",
        cycle_id: str | None = None,
    ) -> PlanAuditResult:
        """Audits a plan against the requirements using litellm."""
        # Construct context
        context_str = "## Reference Requirements\n"
        for fname, content in context_files.items():
            context_str += f"### {fname}\n{content}\n\n"

        plan_str = f"## Proposed Plan\n{plan_details}"

        # Load template
        if phase == "architect":
            template_name = "PLAN_AUDITOR_ARCHITECT.md"
            template_vars = {"context": context_str, "plan": plan_str}
        else:
            template_name = "PLAN_AUDITOR_CODER.md"
            template_vars = {
                "context": context_str,
                "plan": plan_str,
                "cycle_id": cycle_id or "XX",
                "cycle_ref": f"CYCLE {cycle_id}" if cycle_id else "THIS CYCLE",
            }

        user_prompt = settings.read_template(template_name)
        for key, value in template_vars.items():
            user_prompt = user_prompt.replace(f"{{{key}}}", value)

        PlanAuditResult.model_json_schema()

        try:
            response = await litellm.acompletion(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            content = response.choices[0].message.content
            if not content:
                return PlanAuditResult(
                    status="REJECTED",
                    reason="LLM returned empty response",
                )
            return PlanAuditResult.model_validate_json(content)
        except Exception as e:
            return PlanAuditResult(
                status="REJECTED",
                reason=f"Audit process failed: {e}",
            )
