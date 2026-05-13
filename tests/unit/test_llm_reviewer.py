import os
from unittest.mock import MagicMock, patch

import pytest

from src.services.llm_reviewer import LLMReviewer


@pytest.fixture
def reviewer() -> LLMReviewer:
    with (
        patch.dict(
            os.environ, {"OPENAI_API_KEY": "mock", "JULES_API_KEY": "mock", "E2B_API_KEY": "mock"}
        ),
        patch("src.config.Settings.validate_api_keys", return_value=None),
    ):
        return LLMReviewer()


@pytest.mark.asyncio
async def test_review_code_success(reviewer: LLMReviewer) -> None:
    """Test successful code review call."""
    target_files = {"main.py": "print('hello')"}
    context_files = {"spec.md": "# Spec"}
    instruction = "Review this code."
    model = "test-model"

    from src.domain_models import AuditorReport

    valid_json = AuditorReport(
        is_passed=True, summary="Refactored code", issues=[]
    ).model_dump_json()

    # Mock litellm.acompletion
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = valid_json

    with patch(
        "src.services.llm_reviewer.litellm.acompletion", return_value=mock_response
    ) as mock_completion:
        # UPDATED SIGNATURE: target_files, context_docs, instruction, model
        result = await reviewer.review_code(target_files, context_files, instruction, model)

        assert "REVIEW_PASSED" in result
        assert "Refactored code" in result
        mock_completion.assert_called_once()

        # Verify prompt structure in call args
        call_kwargs = mock_completion.call_args.kwargs
        messages = call_kwargs["messages"]
        prompt = messages[1]["content"]

        # Verify strict separation markers
        assert "🚫 READ-ONLY CONTEXT" in prompt
        assert "🎯 AUDIT TARGET" in prompt
        assert "File: spec.md (READ-ONLY SPECIFICATION)" in prompt
        assert "File: main.py (AUDIT TARGET)" in prompt


@pytest.mark.asyncio
async def test_review_code_api_failure(reviewer: LLMReviewer) -> None:
    """Test error handling when API fails."""
    target_files = {"main.py": "content"}
    context_files: dict[str, str] = {}

    with patch("src.services.llm_reviewer.litellm.acompletion", side_effect=Exception("API Error")):
        result = await reviewer.review_code(target_files, context_files, "inst", "model")

        assert "REVIEW_FAILED" in result
        assert "API Error" in result
