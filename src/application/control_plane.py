"""Control-plane use cases that acknowledge Studio tasks before provider replies arrive."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from src.application.harness import AgentHarness
from src.domain.agent import AgentDescriptor, AgentReply, AgentSession, AgentSource, ConversationId
from src.infrastructure.local_store import JsonChatStateStore, LocalEventStore


def descriptor_payload(descriptor: AgentDescriptor) -> dict[str, Any]:
    """Serialize agent metadata at the HTTP boundary."""

    return {**asdict(descriptor), "capabilities": list(descriptor.capabilities)}


def source_payload(source: AgentSource) -> dict[str, Any]:
    """Serialize normalized source data at the HTTP boundary."""

    return {**asdict(source), "branches": list(source.branches)}


def session_payload(session: AgentSession) -> dict[str, Any]:
    """Serialize normalized session data at the HTTP boundary."""

    return asdict(session)


class LocalControlPlane:
    """Coordinate web requests, durable local state, events, and the agent harness."""

    def __init__(
        self,
        harness: AgentHarness,
        state_store: JsonChatStateStore,
        events: LocalEventStore,
    ) -> None:
        self.harness = harness
        self.state_store = state_store
        self.events = events
        self._inflight: dict[str, asyncio.Task[None]] = {}

    def _state_payload(self, conversation_id: ConversationId) -> dict[str, Any]:
        state = self.harness.state_for(conversation_id)
        return {
            "conversation_id": str(conversation_id),
            "active_agent_id": state.active_agent_id,
            "session_name": state.session_name,
            "selected_source": source_payload(state.selected_source) if state.selected_source else None,
            "selected_branch": state.selected_branch,
        }

    async def dashboard(self, conversation_id: ConversationId) -> dict[str, Any]:
        """Return a compact local overview for the dashboard view."""

        event_summary = await self.events.summary()
        return {
            "state": self._state_payload(conversation_id),
            "agents": [descriptor_payload(item) for item in self.harness.registry.descriptors()],
            "event_summary": event_summary,
            "recent_events": await self.events.recent(limit=12),
        }

    async def agents(self, conversation_id: ConversationId) -> dict[str, Any]:
        """List registered agent descriptors and mark the active selection."""

        active_id = self.harness.state_for(conversation_id).active_agent_id
        return {
            "active_agent_id": active_id,
            "agents": [descriptor_payload(item) for item in self.harness.registry.descriptors()],
        }

    async def select_agent(self, conversation_id: ConversationId, agent_id: str) -> dict[str, Any]:
        """Select an agent and record the routing decision locally."""

        descriptor = self.harness.select_agent(conversation_id, agent_id)
        self.state_store.save()
        await self.events.record(
            "agent.selected",
            conversation_id=conversation_id,
            agent_id=descriptor.agent_id,
            summary=f"Selected {descriptor.display_name}",
        )
        return {"agent": descriptor_payload(descriptor), "state": self._state_payload(conversation_id)}

    async def sources(self, conversation_id: ConversationId) -> list[dict[str, Any]]:
        """List sources belonging to the currently selected agent."""

        sources = await self.harness.list_sources(conversation_id)
        return [source_payload(source) for source in sources]

    async def select_source(
        self, conversation_id: ConversationId, source_name: str, branch: str | None = None
    ) -> dict[str, Any]:
        """Select a source and optional branch after validating it against the agent."""

        available_sources = await self.harness.list_sources(conversation_id)
        source = next((item for item in available_sources if item.name == source_name), None)
        if source is None:
            raise LookupError(f"Unknown source: {source_name}")
        selected_branch = branch or source.default_branch
        if selected_branch and source.branches and selected_branch not in source.branches:
            raise LookupError(f"Unknown branch {selected_branch!r} for source {source_name!r}")
        state = self.harness.state_for(conversation_id)
        state.selected_source = source
        state.selected_branch = selected_branch
        self.state_store.reset_session(conversation_id)
        await self.events.record(
            "source.selected",
            conversation_id=conversation_id,
            agent_id=state.active_agent_id,
            summary=f"Selected {source.label}",
            data={"source_name": source.name, "branch": selected_branch},
        )
        return self._state_payload(conversation_id)

    async def sessions(self, conversation_id: ConversationId) -> list[dict[str, Any]]:
        """List remote sessions through the active agent."""

        return [session_payload(item) for item in await self.harness.list_sessions(conversation_id)]

    async def session(self, conversation_id: ConversationId, session_name: str) -> dict[str, Any]:
        """Return one remote session from the active agent."""

        return session_payload(await self.harness.get_session(conversation_id, session_name))

    async def activities(self, conversation_id: ConversationId, session_name: str) -> list[dict[str, Any]]:
        """Return activity history for one remote session."""

        return await self.harness.list_activities(conversation_id, session_name)

    async def submit_message(self, conversation_id: ConversationId, prompt: str) -> dict[str, Any]:
        """Accept one Studio task and record its eventual provider result in the journal."""

        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ValueError("Prompt must not be empty.")
        conversation_key = str(conversation_id)
        if task := self._inflight.get(conversation_key):
            if not task.done():
                raise ValueError("Jules is still processing the previous task in this conversation.")
        state = self.harness.state_for(conversation_id)
        submitted = await self.events.record(
            "message.submitted",
            conversation_id=conversation_id,
            agent_id=state.active_agent_id,
            session_name=state.session_name,
            summary=clean_prompt[:160],
            data={"prompt": clean_prompt},
        )
        task = asyncio.create_task(
            self._complete_message(
                conversation_id=conversation_id,
                prompt=clean_prompt,
                submitted_event_id=submitted["id"],
            ),
            name=f"agent-message:{conversation_key}",
        )
        self._inflight[conversation_key] = task
        task.add_done_callback(lambda finished: self._discard_inflight(conversation_key, finished))
        return {
            "accepted": True,
            "submission_id": submitted["id"],
            "state": self._state_payload(conversation_id),
        }

    def _discard_inflight(self, conversation_key: str, task: asyncio.Task[None]) -> None:
        """Remove a settled worker without replacing a newer task for the conversation."""

        if self._inflight.get(conversation_key) is task:
            self._inflight.pop(conversation_key, None)

    async def _complete_message(
        self,
        *,
        conversation_id: ConversationId,
        prompt: str,
        submitted_event_id: str,
    ) -> None:
        """Wait for a provider response outside the browser request lifetime."""

        state = self.harness.state_for(conversation_id)
        try:
            reply = await self.harness.ask(conversation_id, prompt)
        except Exception as exc:
            await self.events.record(
                "message.failed",
                conversation_id=conversation_id,
                agent_id=state.active_agent_id,
                session_name=state.session_name,
                summary=str(exc),
                data={
                    "prompt": prompt,
                    "submitted_event_id": submitted_event_id,
                    "error_type": type(exc).__name__,
                },
            )
            return
        self.state_store.save()
        await self.events.record(
            "message.completed",
            conversation_id=conversation_id,
            agent_id=reply.agent_id,
            session_name=reply.session.name if reply.session else state.session_name,
            summary=reply.text[:160],
            data={
                "reply": reply.text,
                "metadata": reply.metadata,
                "submitted_event_id": submitted_event_id,
            },
        )

    async def approve_plan(self, conversation_id: ConversationId, session_name: str) -> None:
        """Approve a session plan and write an explicit local audit event."""

        await self.harness.approve_plan(conversation_id, session_name)
        await self.events.record(
            "plan.approved",
            conversation_id=conversation_id,
            agent_id=self.harness.state_for(conversation_id).active_agent_id,
            session_name=session_name,
            summary="Approved provider plan",
        )

    async def delete_session(self, conversation_id: ConversationId, session_name: str) -> None:
        """Delete a provider session and clear matching local routing state."""

        await self.harness.delete_session(conversation_id, session_name)
        state = self.harness.state_for(conversation_id)
        if state.session_name == session_name:
            self.state_store.reset_session(conversation_id)
        await self.events.record(
            "session.deleted",
            conversation_id=conversation_id,
            agent_id=state.active_agent_id,
            session_name=session_name,
            summary="Deleted provider session",
        )

    async def reset_session(self, conversation_id: ConversationId) -> dict[str, Any]:
        """Start fresh locally without deleting the remote session."""

        self.state_store.reset_session(conversation_id)
        await self.events.record(
            "session.reset",
            conversation_id=conversation_id,
            agent_id=self.harness.state_for(conversation_id).active_agent_id,
            summary="Cleared local active session",
        )
        return self._state_payload(conversation_id)
