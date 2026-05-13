import os

import pytest

from src.domain_models import FixPlanSchema, UatExecutionState
from src.services.auditor_usecase import UATAuditorUseCase
from src.services.llm_reviewer import LLMReviewer
from src.state import CycleState


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_live_uat_auditor_diagnosis(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: Enable LangSmith Tracing for supervision
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    if "LANGSMITH_API_KEY" in os.environ:
        monkeypatch.setenv("LANGCHAIN_API_KEY", os.environ["LANGSMITH_API_KEY"])
    monkeypatch.setenv("LANGCHAIN_PROJECT", "nitpickers-live-test")

    llm_reviewer = LLMReviewer()
    uat_auditor = UATAuditorUseCase(llm_reviewer)

    # Mocking a UatExecutionState and CycleState
    uat_state = UatExecutionState(
        exit_code=1,
        stdout="Running tests...\n",
        stderr="ModuleNotFoundError: No module named 'src.missing_module'",
        artifacts=[],
    )

    from src.state import UATState

    state = CycleState(cycle_id="01")
    state.project_session_id = "proj-session-123"
    state.uat = UATState(uat_execution_state=uat_state)

    # Act
    result = await uat_auditor.execute(state)

    # Assert
    assert "current_fix_plan" in result, "Expected 'current_fix_plan' in result"
    fix_plan = result["current_fix_plan"]

    # Assert it's a valid FixPlanSchema and has at least one patch
    assert isinstance(fix_plan, FixPlanSchema), f"Expected FixPlanSchema, got {type(fix_plan)}"
    assert hasattr(fix_plan, "defect_description")
    assert isinstance(fix_plan.defect_description, str)
    assert len(fix_plan.patches) > 0, "Expected at least one patch in the fix plan"
