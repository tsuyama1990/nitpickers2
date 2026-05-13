import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.jules_client import JulesClient


class TestJulesClientLogic(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Patch dependencies to avoid real API calls or Auth
        self.auth_patcher = patch("google.auth.default", return_value=(MagicMock(), "test-project"))
        self.auth_patcher.start()

        self.env_patcher = patch.dict("os.environ", {"OPENAI_API_KEY": "mock_key"})
        self.env_patcher.start()
        self.config_patcher = patch("src.config.Settings.validate_api_keys", return_value=None)
        self.config_patcher.start()

        # Initialize client
        with patch.object(JulesClient, "__init__", lambda x: None):  # Skip init
            self.client = JulesClient()
            self.client.base_url = "https://mock.api"
            self.client.timeout = 5
            self.client.poll_interval = 0.1  # type: ignore[assignment]
            self.client.console = MagicMock()
            self.client.manager_agent = MagicMock()
            self.client.credentials = MagicMock()
            self.client._get_headers = MagicMock(return_value={})  # type: ignore[method-assign]
            self.client.credentials.token = "mock_token"  # noqa: S105
            self.client._sleep = AsyncMock()  # type: ignore[method-assign]

            # FIX: Add context_builder
            self.client.context_builder = MagicMock()
            self.client.context_builder.build_question_context = AsyncMock(
                return_value="mock context"
            )

            # FIX: Add inquiry handler back since __init__ is skipped
            from src.services.jules.inquiry_handler import JulesInquiryHandler

            self.client.inquiry_handler = JulesInquiryHandler(
                manager_agent=self.client.manager_agent,
                context_builder=self.client.context_builder,
                client_ref=self.client,
            )

            # FIX: Add api_client mock which is now used by wait_for_completion
            self.client.api_client = MagicMock()
            self.client.api_client.api_key = "mock_key"
            self.client.api_client.list_activities_async = AsyncMock(return_value=[])
            self.client.api_client._get_headers = MagicMock(return_value={})

            self.client.test_mode = False
            self.client.git = AsyncMock()

    def tearDown(self) -> None:
        self.auth_patcher.stop()
        self.env_patcher.stop()
        self.config_patcher.stop()


if __name__ == "__main__":
    unittest.main()
