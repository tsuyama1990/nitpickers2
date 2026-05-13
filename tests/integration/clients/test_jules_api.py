import httpx
import pytest
import respx

from src.config import settings
from src.services.jules.api import JulesApiClient


@pytest.fixture
def jules_api_client(monkeypatch: pytest.MonkeyPatch) -> JulesApiClient:
    monkeypatch.setenv("JULES_API_KEY", "dummy_key")
    return JulesApiClient(api_key="dummy_key")


@pytest.mark.asyncio
@respx.mock
async def test_list_activities_async_success(jules_api_client: JulesApiClient) -> None:
    session_id_path = "projects/123/locations/global/sessions/test-session-id"
    url = f"{settings.jules.base_url}/{session_id_path}/{settings.jules.activities_path}"

    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "activities": [
                    {"name": "activity1", "activityType": "runStateChanged", "state": "COMPLETED"}
                ],
                "nextPageToken": "",
            },
        )
    )

    activities = await jules_api_client.list_activities_async(session_id_path)
    assert len(activities) == 1
    assert activities[0]["activityType"] == "runStateChanged"


@pytest.mark.asyncio
@respx.mock
async def test_retry_on_429_transient_failure() -> None:
    # We will test the async dispatcher's retry logic, usually used by _send_message in JulesClient
    # But since that uses retry_on_429 directly, we can test it at the method level.
    from src.services.jules_client import JulesClient

    jules_client = JulesClient()
    session_url = (
        "https://jules.googleapis.com/v1/projects/123/locations/global/sessions/test-session-id"
    )
    url = f"{session_url}{settings.jules.send_message_action}"

    # Mocking first two calls as 503 (transient), then 200 OK
    route = respx.post(url).mock(
        side_effect=[
            httpx.Response(503, text="Service Unavailable"),
            httpx.Response(503, text="Service Unavailable"),
            httpx.Response(200, json={}),
        ]
    )

    # We expect it to succeed after retrying
    await jules_client._send_message(session_url, "test content")
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_retry_on_429_max_retries_exceeded() -> None:
    from src.services.async_dispatcher import MaxRetriesExceededError
    from src.services.jules_client import JulesClient

    jules_client = JulesClient()
    session_url = (
        "https://jules.googleapis.com/v1/projects/123/locations/global/sessions/test-session-id"
    )
    url = f"{session_url}{settings.jules.send_message_action}"

    # Mocking all calls as 429 to trigger MaxRetriesExceededError
    route = respx.post(url).mock(return_value=httpx.Response(429, text="Too Many Requests"))

    with pytest.raises(MaxRetriesExceededError) as exc_info:
        await jules_client._send_message(session_url, "test content")

    assert "Max retries exceeded" in str(exc_info.value)
    assert route.call_count > 1
