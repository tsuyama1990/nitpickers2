import json
import traceback
from typing import Any

from rich.console import Console

from src.config import settings
from src.domain_models import CriticResult
from src.utils import extract_json_from_text

console = Console()


class SelfCriticEvaluator:
    def __init__(self, jules_client: Any) -> None:
        if not jules_client:
            msg = "JulesClient must be injected into SelfCriticEvaluator"
            raise ValueError(msg)
        self.jules = jules_client

    def _extract_raw_text(self, raw_data: dict[str, Any]) -> str:
        outputs = raw_data.get("outputs", [])
        raw_text = ""
        for output in outputs:
            if "pullRequest" in output:
                raw_text += output["pullRequest"].get("description", "")
            if "chatCompletion" in output:
                raw_text += output["chatCompletion"].get("text", "")
            if "text" in output:
                raw_text += output["text"]

        if not raw_text and "activities" in raw_data:
            for act in raw_data["activities"]:
                if "agentMessaged" in act:
                    val = act["agentMessaged"].get("agentMessage")
                    if val:
                        raw_text += val
        return raw_text

    def _parse_critic_result(self, raw_data: dict[str, Any] | None) -> CriticResult:
        if not raw_data:
            return CriticResult(
                is_approved=False,
                vulnerabilities=["No raw data received from Jules."],
                suggestions=[],
            )

        raw_text = self._extract_raw_text(raw_data)

        if not raw_text:
            return CriticResult(is_approved=True, vulnerabilities=[], suggestions=[])

        json_str = extract_json_from_text(raw_text)
        if json_str:
            try:
                data = json.loads(json_str)
                return CriticResult.model_validate(data)
            except Exception as e:
                console.print(
                    f"[yellow]Failed to parse Critic JSON: {e}[/yellow]\n[dim]{json_str}[/dim]"
                )

        is_approved = any(
            pattern in raw_text
            for pattern in ['"is_approved": true', '"is_approved":true', "is_approved: true"]
        )

        return CriticResult(
            is_approved=is_approved,
            vulnerabilities=[]
            if is_approved
            else ["Critic rejected the plan but output was not valid JSON."],
            suggestions=[]
            if is_approved
            else ["Ensure the output follows the requested JSON schema."],
        )

    async def evaluate(
        self, session_id: str, template_name: str = "ARCHITECT_CRITIC_INSTRUCTION.md", **kwargs: Any
    ) -> tuple[CriticResult, str | None, str | None]:
        """
        Interacts with the Jules session to run the Red Team Critic evaluation
        and returns the parsed CriticResult.
        """
        console.print(
            f"[bold magenta]Invoking Self-Critic Evaluator ({template_name})...[/bold magenta]"
        )
        critic_instruction = settings.read_template(template_name)

        # Simple template variable substitution
        for key, val in kwargs.items():
            critic_instruction = critic_instruction.replace(f"{{{{{key}}}}}", str(val))

        try:
            session_url = self.jules._get_session_url(session_id)
            await self.jules._send_message(session_url, critic_instruction)
            console.print("[dim]Waiting for Architect Critic evaluation to complete...[/dim]")

            result = await self.jules.wait_for_completion(session_id, expect_new_work=True)

            if result.get("status") != "success":
                return (
                    CriticResult(
                        is_approved=False,
                        vulnerabilities=["Self-Critic evaluation failed to complete successfully."],
                        suggestions=[],
                    ),
                    None,
                    None,
                )

            raw_result = result.get("raw")
            critic_res = self._parse_critic_result(raw_result)

            # Extract PR info from raw result if available
            pr_url = None
            branch_name = None
            if raw_result and "outputs" in raw_result:
                for output in raw_result["outputs"]:
                    if "pullRequest" in output:
                        pr_url = output["pullRequest"].get("url")
                        branch_name = output["pullRequest"].get("branchName")
                        break
        except Exception as e:
            tb = traceback.format_exc()
            console.print(f"[bold red]Critic Evaluation failed: {e}[/bold red]")
            console.print(f"[dim red]{tb}[/dim red]")
            return (
                CriticResult(
                    is_approved=False,
                    vulnerabilities=[
                        "SYSTEM_ERROR: Self-critic evaluation encountered a network timeout or system failure. Please remain on standby while the system retries."
                    ],
                    suggestions=[],
                ),
                None,
                None,
            )
        else:
            return critic_res, pr_url, branch_name
