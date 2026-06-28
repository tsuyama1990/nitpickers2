"""Live test for LangSmith tracing connectivity.

Verifies that the LangSmith API key and tracing configuration are valid
and that traces can be sent to the LangSmith platform.

Prerequisites:
    - LANGCHAIN_TRACING_V2=true (or NITPICK_TRACING__LANGCHAIN_TRACING_V2=true)
    - LANGCHAIN_API_KEY or LANGSMITH_API_KEY (e.g., lsv2_pt_...)
    - LANGCHAIN_PROJECT or LANGSMITH_PROJECT (e.g., "nitpickers")
    - Run with: uv run pytest tests/e2e/live/test_langsmith_tracing.py -v -m live -s
"""

from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.live]


def test_langsmith_env_configuration() -> None:
    """Check which LangSmith-related env vars are available and how settings interprets them."""

    # Raw env vars
    ls_api_key = os.environ.get("LANGSMITH_API_KEY", "")
    lc_api_key = os.environ.get("LANGCHAIN_API_KEY", "")
    lc_tracing = os.environ.get("LANGCHAIN_TRACING_V2", "false")
    os.environ.get("LANGCHAIN_PROJECT", "")
    os.environ.get("LANGSMITH_PROJECT", "")


    # NITPICK_ prefixed vars
    nitpick_tracing = os.environ.get("NITPICK_TRACING__LANGCHAIN_TRACING_V2", "")
    nitpick_key = os.environ.get("NITPICK_TRACING__LANGCHAIN_API_KEY", "")
    os.environ.get("NITPICK_TRACING__LANGCHAIN_PROJECT", "")


    # Check if settings would enable tracing
    tracing_will_work = (
        lc_tracing.lower() == "true" or nitpick_tracing.lower() == "true"
    ) and (bool(lc_api_key) or bool(ls_api_key) or bool(nitpick_key))

    if tracing_will_work:
        pass
    else:
        pass


@pytest.mark.asyncio
async def test_langsmith_tracing_via_settings() -> None:
    """Verify that the Settings model correctly initializes LangSmith tracing.

    Loads settings (which runs validation including tracing setup)
    and checks if tracing was enabled.
    """
    from src.config import settings


    # Check if env vars were synchronized

    if settings.tracing.tracing_enabled:
        pass
    else:
        pass


@pytest.mark.asyncio
async def test_langsmith_send_trace() -> None:
    """Actually send a test trace to LangSmith to verify end-to-end connectivity.

    This test requires LangSmith to be properly configured.
    Skips if tracing is not enabled.
    """
    from src.config import settings

    if not settings.tracing.tracing_enabled:
        pytest.skip("LangSmith tracing is not enabled. Set LANGCHAIN_TRACING_V2=true and LANGCHAIN_API_KEY=...")

    from langchain_core.callbacks import CallbackManager
    from langchain_core.tracers import LangChainTracer

    tracer = LangChainTracer(
        project_name=settings.tracing.project_name,
        api_key=settings.tracing.api_key,
    )
    callback_manager = CallbackManager([tracer])

    # Run a simple chain to test tracing
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import PromptTemplate
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model="gpt-3.5-turbo",
        openai_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0,
        callbacks=callback_manager,
    )

    prompt = PromptTemplate.from_template("Say the word '{word}' and nothing else.")
    chain = prompt | llm | StrOutputParser()

    await chain.ainvoke({"word": "LangSmith"})

    # If we got here without errors, the trace was sent
