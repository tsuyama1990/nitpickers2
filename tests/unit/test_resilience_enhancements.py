import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.jules_session_nodes import JulesSessionNodes
from src.jules_session_state import JulesSessionState, SessionStatus


@pytest.mark.asyncio
async def test_monitor_session_success() -> None:
    """Verify that monitor_session identifies COMPLETED state."""
    mock_client = AsyncMock()
    mock_client.list_activities.return_value = []
    mock_client.get_session_state.return_value = "COMPLETED"
    mock_client._send_message.return_value = None
    mock_client._get_headers.return_value = {}

    nodes = JulesSessionNodes(mock_client)

    loop = asyncio.get_running_loop()
    # Explicitly set status to MONITORING and jules_state to something else
    state = JulesSessionState(
        session_url="http://test/session",
        start_time=loop.time(),
        status=SessionStatus.MONITORING,
        jules_state="IN_PROGRESS",
    )

    with (
        patch("src.jules_session_nodes.httpx") as mock_httpx,
        patch("src.config.settings") as mock_settings,
    ):
        mock_settings.jules.monitor_batch_size = 1
        mock_settings.jules.monitor_poll_interval_seconds = 0.01
        mock_httpx.codes.OK = 200

        mock_instance = mock_httpx.AsyncClient.return_value
        mock_instance.__aenter__.return_value = mock_instance

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"state": "COMPLETED", "outputs": []}
        mock_instance.get = AsyncMock(return_value=mock_resp)

        # Run
        updates = await nodes.monitor_session(state)

        # Verify updates contains the changes
        assert updates.get("jules_state") == "COMPLETED"
        # status might be missing if it didn't change (though it should have)
        # but jules_state change is enough to verify the loop terminated correctly.


@pytest.mark.asyncio
async def test_monitor_session_recovery_nudge() -> None:
    """Verify that monitor_session sends a recovery nudge on FAILED state."""
    mock_client = AsyncMock()
    mock_client.list_activities.return_value = []
    mock_client.get_session_state.return_value = "FAILED"
    mock_client._send_message.return_value = None
    mock_client._get_headers.return_value = {}

    nodes = JulesSessionNodes(mock_client)

    loop = asyncio.get_running_loop()
    state = JulesSessionState(
        session_url="http://test/session",
        start_time=loop.time(),
        status=SessionStatus.MONITORING,
        jules_state="IN_PROGRESS",
    )

    with (
        patch("src.jules_session_nodes.httpx") as mock_httpx,
        patch("src.config.settings") as mock_settings,
    ):
        mock_settings.jules.monitor_batch_size = 2
        mock_settings.jules.monitor_poll_interval_seconds = 0.01
        mock_httpx.codes.OK = 200

        mock_instance = mock_httpx.AsyncClient.return_value
        mock_instance.__aenter__.return_value = mock_instance

        # 1st poll: FAILED
        # 2nd poll: STILL FAILED (now should actually fail)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"state": "FAILED", "outputs": []}
        mock_instance.get = AsyncMock(return_value=mock_resp)

        # Run
        updates = await nodes.monitor_session(state)

        # Verify
        assert mock_client._send_message.called
        assert "failed unexpectedly" in mock_client._send_message.call_args[0][1]
        assert updates.get("recovery_nudge_sent") is True
        assert updates.get("status") == SessionStatus.FAILED
