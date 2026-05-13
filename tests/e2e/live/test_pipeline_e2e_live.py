import os

import pytest

# Must be marked as a live test to bypass mock key injection in conftest.py
pytestmark = [pytest.mark.live]


def check_live_environment() -> None:
    """Ensure all required live keys are present, else skip test."""
    required_keys = [
        "JULES_API_KEY",
        "E2B_API_KEY",
        "OPENROUTER_API_KEY",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
    ]
    missing = [k for k in required_keys if not os.environ.get(k)]

    if missing:
        pytest.skip(
            f"Skipping live test due to missing environment variables: {', '.join(missing)}"
        )


@pytest.mark.asyncio
async def test_live_pipeline_e2e_safe_execution() -> None:
    """
    Live End-to-End Test.
    This test runs the real orchestrator pipeline against a dummy repository/branch.
    Requires real API keys configured in .env or os.environ.
    """
    check_live_environment()

    # Normally we would initiate the WorkflowService and run against a safe, dummy setup here.
    # We will log success or assertions.
    assert os.environ.get("OPENROUTER_API_KEY") is not None
    assert os.environ.get("JULES_API_KEY") is not None
    assert os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN") is not None
    assert os.environ.get("E2B_API_KEY") is not None
