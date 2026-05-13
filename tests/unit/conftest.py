from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.config import Settings


@pytest.fixture(autouse=True)
def mock_settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Mock the global settings object and env vars for all unit tests."""
    # Pre-emptively set environment variables to avoid pydantic-ai import errors
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy_key_for_test")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_key_for_test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy_key_for_test")
    monkeypatch.setenv("JULES_API_KEY", "dummy_key_for_test")
    monkeypatch.setenv("E2B_API_KEY", "dummy_key_for_test")

    # Set required settings for AgentsConfig to pass validation
    monkeypatch.setenv("NITPICK_AUDITOR_MODEL", "openai:gpt-4o")
    monkeypatch.setenv("NITPICK_QA_ANALYST_MODEL", "openai:gpt-4o")

    # Set required settings for ReviewerConfig to pass validation
    monkeypatch.setenv("NITPICK_REVIEWER__SMART_MODEL", "openai:gpt-4o")
    monkeypatch.setenv("NITPICK_REVIEWER__FAST_MODEL", "openai:gpt-3.5-turbo")

    # Create a default Settings object (using defaults defined in class)
    try:
        real_defaults = Settings()
    except Exception:
        # Fallback if creation fails (e.g. missing required env vars if any)
        real_defaults = MagicMock()

    with patch("src.config.settings", real_defaults):
        yield real_defaults


@pytest.fixture
def mock_file_patcher() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_jules() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_reviewer() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_git() -> MagicMock:
    return MagicMock()
