"""Unit tests for the provider-neutral agent harness."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from src.application.harness import AgentHarness, AgentRegistry
from src.domain.agent import AgentDescriptor, AgentReply, AgentSession, AgentSource


class FakeAgent:
    """Small deterministic adapter used to test application orchestration."""

    def __init__(self, agent_id: str) -> None:
        self._descriptor = AgentDescriptor(
            agent_id=agent_id,
            display_name=agent_id.title(),
            description=f"Fake {agent_id} agent",
            capabilities=("test",),
        )
        self.prompts: list[str] = []

    @property
    def descriptor(self) -> AgentDescriptor:
        """Return fake agent metadata."""

        return self._descriptor

    async def ask(self, chat_id: int, prompt: str, *, state: Any) -> AgentReply:
        """Record a prompt and return a normalized response."""

        self.prompts.append(prompt)
        state.session_name = f"sessions/{self._descriptor.agent_id}-{chat_id}"
        return AgentReply(text=f"{self._descriptor.agent_id}: {prompt}", agent_id=self._descriptor.agent_id)

    async def list_sources(self) -> list[AgentSource]:
        """Return no fake sources."""

        return []

    async def get_session(self, session_name: str) -> AgentSession:
        """Return a minimal fake session."""

        return AgentSession(name=session_name, identifier=session_name)

    async def list_activities(self, session_name: str) -> list[dict[str, Any]]:
        """Return no fake activities."""

        return []

    async def list_sessions(self) -> list[AgentSession]:
        """Return no fake sessions."""

        return []

    async def approve_plan(self, session_name: str) -> None:
        """Accept a fake plan approval."""

    async def delete_session(self, session_name: str) -> None:
        """Accept a fake session deletion."""

    async def close(self) -> None:
        """Release no fake resources."""


class HarnessTests(unittest.IsolatedAsyncioTestCase):
    """Verify routing and state boundaries independently of Telegram or Jules."""

    async def test_routes_to_default_agent_and_tracks_session(self) -> None:
        first = FakeAgent("first")
        second = FakeAgent("second")
        harness = AgentHarness(AgentRegistry([first, second], default_agent_id="first"))

        reply = await harness.ask(7, "hello")

        self.assertEqual(reply.text, "first: hello")
        self.assertEqual(harness.state_for(7).session_name, "sessions/first-7")
        self.assertEqual(first.prompts, ["hello"])
        self.assertEqual(second.prompts, [])

    async def test_switching_agent_clears_provider_session(self) -> None:
        first = FakeAgent("first")
        second = FakeAgent("second")
        harness = AgentHarness(AgentRegistry([first, second], default_agent_id="first"))
        await harness.ask(7, "before")

        descriptor = harness.select_agent(7, "second")
        reply = await harness.ask(7, "after")

        self.assertEqual(descriptor.agent_id, "second")
        self.assertEqual(reply.agent_id, "second")
        self.assertEqual(harness.state_for(7).active_agent_id, "second")
        self.assertEqual(harness.state_for(7).session_name, "sessions/second-7")
    async def test_same_chat_requests_are_serialized(self) -> None:
        first = FakeAgent("first")
        harness = AgentHarness(AgentRegistry([first]))
        order: list[str] = []

        async def run(prompt: str) -> None:
            async with harness.store.lock_for(11):
                order.append(f"start:{prompt}")
                await asyncio.sleep(0.01)
                order.append(f"end:{prompt}")

        await asyncio.gather(run("a"), run("b"))

        self.assertEqual(order, ["start:a", "end:a", "start:b", "end:b"])


if __name__ == "__main__":
    unittest.main()
