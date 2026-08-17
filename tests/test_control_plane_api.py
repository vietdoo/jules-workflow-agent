"""Contract tests for the local FastAPI control-plane adapter."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
            text="Jules accepted this task and is reporting provider activity.",
            agent_id="fake",
            session=AgentSession(
                name="sessions/fake-session",
                identifier="fake-session",
                title="Fake work",
                state="IN_PROGRESS",
                state_label="In Progress",
            ),
            metadata={"pending": True},
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
        return [
            {
                "name": f"{session_name}/activities/plan",
                "kind": "plan.proposed",
                "summary": "Jules prepared an implementation plan.",
            },
            {
                "name": f"{session_name}/activities/message",
                "kind": "agent.message",
                "summary": "Provider completed work.",
                "terminal": True,
            },
        ]

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
        """The browser receives immediate acceptance and later mirrors provider activity."""

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
                for _ in range(100):
                    events = client.get("/api/events?conversation_id=web:local").json()
                    if any(item["type"] == "message.completed" for item in events):
                        break
                    time.sleep(0.01)
                event_types = [item["type"] for item in events]
                self.assertIn("provider.plan.proposed", event_types)
                self.assertIn("message.completed", event_types)
                completion = next(item for item in events if item["type"] == "message.completed")
                self.assertEqual(completion["data"]["reply"], "Provider completed work.")
                self.assertEqual(completion["data"]["submitted_event_id"], reply.json()["submission_id"])
                self.assertNotIn("message.failed", event_types)

                encoded_session = quote("sessions/fake-session", safe="")
                activities = client.get(f"/api/sessions/{encoded_session}/activities?conversation_id=web:local")
                self.assertEqual(activities.status_code, 200)
                self.assertEqual(activities.json()[0]["kind"], "plan.proposed")

                reset = client.post("/api/sessions/reset", json={"conversation_id": "web:local"})
                self.assertEqual(reset.status_code, 200)
                attached = client.post(
                    f"/api/sessions/{encoded_session}/attach",
                    json={"conversation_id": "web:local"},
                )
                self.assertEqual(attached.status_code, 200)
                self.assertEqual(attached.json()["session_name"], "sessions/fake-session")

                other_events = client.get("/api/events?conversation_id=web:other").json()
                self.assertEqual(other_events, [])


if __name__ == "__main__":
    unittest.main()
