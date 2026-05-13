from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.jules_client import JulesClient, JulesTimeoutError


@pytest.fixture
def mock_client() -> Generator[JulesClient, None, None]:
    # Use dummy key to pass init
    with (
        patch("src.services.jules_client.settings.JULES_API_KEY", "dummy"),
        patch("src.config.Settings.validate_api_keys", return_value=None),
        patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "mock_key", "JULES_API_KEY": "mock", "E2B_API_KEY": "mock"},
        ),
        patch("src.services.jules_client.get_manager_agent") as mock_agent,
    ):
        AsyncMock()
        # Initialize client
        with patch.object(JulesClient, "__init__", lambda x: None):  # Skip init
            client = JulesClient()
            client.base_url = "https://mock.api"
            client.timeout = 5.0  # type: ignore[assignment]
            client.poll_interval = 0.1  # type: ignore[assignment]
            client.console = MagicMock()
            client.manager_agent = mock_agent
            client.manager_agent.run = AsyncMock(return_value=MagicMock(output="Manager Reply"))
            client.credentials = MagicMock()
            client._get_headers = MagicMock(return_value={})  # type: ignore[method-assign]
            client.credentials.token = "mock_token"  # noqa: S105
            client._sleep = AsyncMock()  # type: ignore[method-assign]

            # FIX: Add context_builder
            client.context_builder = MagicMock()
            client.context_builder.build_question_context = AsyncMock(return_value="mock context")

            from src.services.jules.inquiry_handler import JulesInquiryHandler

            client.inquiry_handler = JulesInquiryHandler(
                manager_agent=client.manager_agent,
                context_builder=client.context_builder,
                client_ref=client,
            )

            # FIX: Add api_client mock which is now used by wait_for_completion
            client.api_client = MagicMock()
            client.api_client.api_key = "mock_key"
            client.api_client.list_activities_async = AsyncMock(return_value=[])
            client.api_client._get_headers = MagicMock(return_value={})

            client.test_mode = False
            client.git = AsyncMock()
            yield client


@pytest.fixture
def mock_httpx() -> Generator[AsyncMock, None, None]:
    with patch("httpx.AsyncClient") as mock_cls:
        mock_instance = AsyncMock()
        # Setup context manager
        mock_cls.return_value.__aenter__.return_value = mock_instance
        mock_cls.return_value.__aexit__.return_value = None
        yield mock_instance


@pytest.mark.asyncio
async def test_wait_for_completion_loop_success(
    mock_client: JulesClient, mock_httpx: AsyncMock
) -> None:
    """Test polling loop finds PR after few tries."""
    mock_client._sleep = AsyncMock()  # type: ignore[method-assign]

    # Sequence: IN_PROGRESS -> IN_PROGRESS -> COMPLETED
    # NOTE: list_activities also calls GET, we need to handle that or distinguish by URL

    expected_calls = 2

    async def get_side_effect(url: str, **_kwargs: Any) -> MagicMock:
        if "activities" in url:
            return MagicMock(status_code=200, json=lambda: {"activities": []})

        # Session Status
        # We use sleep call count to decide iteration
        resp = AsyncMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status = MagicMock()

        if mock_client._sleep.call_count < expected_calls:  # type: ignore[attr-defined]
            resp.json = MagicMock(return_value={"state": "IN_PROGRESS"})
            return resp

        resp.json = MagicMock(
            return_value={
                "state": "COMPLETED",
                "outputs": [{"pullRequest": {"url": "https://pr"}}],
            }
        )
        return resp

    mock_httpx.get.side_effect = get_side_effect

    mock_client.list_activities = AsyncMock(return_value=[])  # type: ignore[method-assign]
    result = await mock_client.wait_for_completion("sessions/123")
    assert result.get("pr_url") == "https://pr" or result.get("status") == "success"
    assert mock_client._sleep.call_count >= expected_calls


@pytest.mark.asyncio
async def test_wait_for_completion_timeout(mock_client: JulesClient, mock_httpx: AsyncMock) -> None:
    """Test timeout behaves correctly."""
    mock_client.timeout = 0.001  # type: ignore[assignment]
    mock_client._sleep = AsyncMock()  # type: ignore[method-assign]

    # Always IN_PROGRESS (never completes → triggers timeout)
    mock_response = AsyncMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"state": "IN_PROGRESS"})

    async def get_mock(url: str, **kwargs: Any) -> Any:
        return mock_response

    mock_httpx.get.side_effect = get_mock

    with pytest.raises(JulesTimeoutError):
        await mock_client.wait_for_completion("sessions/123")


@pytest.mark.asyncio
async def test_interactive_inquiry_handling(
    mock_client: JulesClient, mock_httpx: AsyncMock
) -> None:
    """Test handling of Jules inquiry."""
    mock_client._sleep = AsyncMock()  # type: ignore[method-assign]

    mock_response = MagicMock()
    mock_response.output = "My Answer"
    mock_client.manager_agent.run = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]

    mock_client.inquiry_handler.context_builder = MagicMock()
    mock_client.list_activities = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"name": "act1", "agentMessaged": {"agentMessage": "Should I continue?"}}]
    )
    mock_client.inquiry_handler.context_builder.build_question_context = AsyncMock(
        return_value="mock context"
    )

    async def get_side_effect(url: str, **_kwargs: Any) -> MagicMock:
        if "activities" in url:
            # Return question on first call (before sleep/reply)
            # We check if post has been called to determine if we answered
            if not mock_httpx.post.called:
                mock_activity = MagicMock()
                mock_activity.status_code = 200
                mock_activity.json.return_value = {
                    "activities": [
                        {
                            "name": "act1",
                            "agentMessaged": {"agentMessage": "Should I continue?"},
                        }
                    ]
                }
                return mock_activity
            mock_empty = MagicMock()
            mock_empty.status_code = 200
            mock_empty.json.return_value = {"activities": [{"name": "act1"}]}
            return mock_empty

        # Session Status
        if not mock_httpx.post.called:
            mock_awaiting = MagicMock()
            mock_awaiting.status_code = 200
            mock_awaiting.json.return_value = {"state": "AWAITING_USER_FEEDBACK"}
            return mock_awaiting

        mock_completed = MagicMock()
        mock_completed.status_code = 200
        mock_completed.json.return_value = {
            "state": "COMPLETED",
            "outputs": [{"pullRequest": {"url": "https://pr"}}],
        }
        return mock_completed

    mock_httpx.get.side_effect = get_side_effect
    mock_httpx.post.return_value.status_code = 200
    mock_httpx.post.return_value.raise_for_status = MagicMock()

    result = await mock_client.wait_for_completion("sessions/123")

    assert result["pr_url"] == "https://pr"
    assert mock_client.manager_agent.run.called
    assert mock_httpx.post.called
