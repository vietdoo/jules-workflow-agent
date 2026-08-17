"""Contract tests for the local FastAPI control-plane adapter."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from apps.api.app import create_app
from src.application.control_plane import LocalControlPlane
from src.application.harness import AgentHarness, AgentRegistry
from src.config import Settings
from src.domain.agent import AgentDescriptor, AgentReply, AgentSession, AgentSource
from src.infrastructure.local_store import JsonChatStateStore, LocalEventStore


class FakeAgent:
    """Small provider adapter used to exercise transport behavior deterministically."""

    descriptor = AgentDescriptor(
        agent_id="fake",
        display_name="Fake Agent",
        description="A deterministic API-test adapter.",
        icon="F",
        capabilities=("test",),
    )

    async def ask(self, conversation_id: str | int, prompt: str, *, state: Any) -> AgentReply:
        state.session_name = "sessions/fake-session"
        return AgentReply(
            text=f"Echo: {prompt}",
            agent_id="fake",
            session=AgentSession(
                name="sessions/fake-session",
                identifier="fake-session",
                title="Fake work",
                state="IN_PROGRESS",
                state_label="In Progress",
            ),
        )

    async def list_sources(self) -> list[AgentSource]:
        return [
            AgentSource(
                name="sources/fake",
                label="acme/fake",
                default_branch="main",
                branches=("main",),
            )
        ]

    async def get_session(self, session_name: str) -> AgentSession:
        return AgentSession(
            name=session_name,
            identifier="fake-session",
            title="Fake work",
            state="IN_PROGRESS",
            state_label="In Progress",
        )

    async def list_activities(self, session_name: str) -> list[dict[str, Any]]:
        return [{"name": f"{session_name}/activities/1", "agentMessaged": {"agentMessage": "ok"}}]

    async def list_sessions(self) -> list[AgentSession]:
        return [await self.get_session("sessions/fake-session")]

    async def approve_plan(self, session_name: str) -> None:
        return None

    async def delete_session(self, session_name: str) -> None:
        return None

    async def close(self) -> None:
        return None


def make_settings() -> Settings:
    """Return complete local settings without reading a real environment file."""

    return Settings(
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
        agent_default_id="fake",
        web_api_host="127.0.0.1",
        web_api_port=8090,
        web_cors_origins=("http://127.0.0.1:3000",),
        local_data_dir="runtime",
    )


class ControlPlaneApiTests(unittest.TestCase):
    """Verify browser-facing API shapes and local transcript recording."""

    def test_dashboard_context_prompt_and_journal_workflow(self) -> None:
        """The browser receives an acceptance before its journal records the agent reply."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JsonChatStateStore(root)
            harness = AgentHarness(AgentRegistry([FakeAgent()], default_agent_id="fake"), store)
            plane = LocalControlPlane(harness, store, LocalEventStore(root))
            app = create_app(make_settings(), control_plane=plane)
            with TestClient(app) as client:
                dashboard = client.get("/api/dashboard").json()
                self.assertEqual(dashboard["state"]["active_agent_id"], "fake")

                source = client.put(
                    "/api/sources/active",
                    json={"conversation_id": "web:local", "source_name": "sources/fake", "branch": "main"},
                )
                self.assertEqual(source.status_code, 200)

                reply = client.post(
                    "/api/messages",
                    json={"conversation_id": "web:local", "prompt": "Summarize the change."},
                )
                self.assertEqual(reply.status_code, 200)
                self.assertTrue(reply.json()["accepted"])
                self.assertTrue(reply.json()["submission_id"])

                events: list[dict[str, Any]] = []
                for _ in range(20):
                    events = client.get("/api/events").json()
                    if any(item["type"] == "message.completed" for item in events):
                        break
                    time.sleep(0.01)
                self.assertEqual([item["type"] for item in events[:2]], ["message.completed", "message.submitted"])
                self.assertEqual(events[0]["data"]["reply"], "Echo: Summarize the change.")
                self.assertEqual(events[0]["data"]["submitted_event_id"], reply.json()["submission_id"])


if __name__ == "__main__":
    unittest.main()
