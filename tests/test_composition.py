"""Composition-root tests that do not contact Telegram or Jules."""

from __future__ import annotations

import unittest

from src.application.harness import AgentHarness
from src.config import Settings
from src.main import build_application


class CompositionTests(unittest.IsolatedAsyncioTestCase):
    """Verify application wiring and resource ownership."""

    async def test_application_registers_harness(self) -> None:
        settings = Settings(
            telegram_bot_token="123456:TEST_TOKEN",
            jules_api_key="test-key",
            jules_api_url="https://jules.googleapis.com/v1alpha",
            webhook_url=None,
            port=8080,
            jules_timeout_seconds=60.0,
            jules_poll_interval_seconds=2.0,
            jules_reply_timeout_seconds=120.0,
            log_level="INFO",
            webhook_secret_token=None,
            jules_source=None,
            jules_starting_branch="main",
            jules_require_plan_approval=False,
            jules_automation_mode=None,
            agent_default_id="jules",
            web_api_host="127.0.0.1",
            web_api_port=8090,
            web_cors_origins=("http://127.0.0.1:3000",),
            local_data_dir="runtime",
        )
        application = build_application(settings)

        self.assertIsInstance(application.bot_data["agent_harness"], AgentHarness)
        self.assertEqual(application.bot_data["agent_harness"].active_descriptor(1).agent_id, "jules")
        await application.bot_data["agent_harness"].close()


if __name__ == "__main__":
    unittest.main()
