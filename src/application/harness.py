"""Application-level agent harness and replaceable conversation state."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from src.domain.agent import (
    AgentAdapter,
    AgentDescriptor,
    AgentReply,
    AgentSession,
    AgentSource,
    ConversationId,
)

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ChatState:
    """Mutable state that associates one interface conversation with one agent session."""

    active_agent_id: str = ""
    session_name: str | None = None
    selected_source: AgentSource | None = None
    selected_branch: str | None = None
    source_options: dict[str, AgentSource] = field(default_factory=dict)
    branch_options: dict[str, str] = field(default_factory=dict)
    session_options: dict[str, str] = field(default_factory=dict)
    agent_options: dict[str, str] = field(default_factory=dict)


class StateStore(Protocol):
    """Port for process-local or durable state keyed by a conversation identity."""

    def state_for(self, conversation_id: ConversationId, *, default_agent_id: str = "") -> ChatState:
        """Return the state associated with a conversation."""
        ...

    def lock_for(self, conversation_id: ConversationId) -> asyncio.Lock:
        """Return the lock used to serialize agent work for a conversation."""
        ...

    def reset_session(self, conversation_id: ConversationId) -> None:
        """Clear the remote session association while preserving agent selection."""
        ...


class ChatStateStore:
    """Small in-memory state store appropriate for a single process."""

    def __init__(self) -> None:
        self._states: dict[ConversationId, ChatState] = {}
        self._locks: defaultdict[ConversationId, asyncio.Lock] = defaultdict(asyncio.Lock)

    def state_for(self, conversation_id: ConversationId, *, default_agent_id: str = "") -> ChatState:
        """Return or initialize state for one Telegram or web conversation."""

        state = self._states.setdefault(conversation_id, ChatState(active_agent_id=default_agent_id))
        if not state.active_agent_id:
            state.active_agent_id = default_agent_id
        return state

    def lock_for(self, conversation_id: ConversationId) -> asyncio.Lock:
        """Return a process-local lock that serializes one conversation."""

        return self._locks[conversation_id]

    def reset_session(self, conversation_id: ConversationId) -> None:
        """Clear remote session selection while preserving the selected agent."""

        state = self.state_for(conversation_id)
        state.session_name = None
        state.source_options.clear()
        state.branch_options.clear()
        state.session_options.clear()

    def reset(self, conversation_id: ConversationId) -> None:
        """Clear all process-local state and locking data for a conversation."""

        self._states.pop(conversation_id, None)
        self._locks.pop(conversation_id, None)


class AgentRegistry:
    """Resolve registered agent adapters by stable ID."""

    def __init__(self, adapters: Iterable[AgentAdapter], *, default_agent_id: str | None = None) -> None:
        self._adapters: dict[str, AgentAdapter] = {}
        for adapter in adapters:
            agent_id = adapter.descriptor.agent_id.strip()
            if not agent_id:
                raise ValueError("Agent IDs must not be empty.")
            if agent_id in self._adapters:
                raise ValueError(f"Duplicate agent ID: {agent_id}")
            self._adapters[agent_id] = adapter
        if not self._adapters:
            raise ValueError("At least one agent adapter must be registered.")
        self._default_agent_id = default_agent_id or next(iter(self._adapters))
        if self._default_agent_id not in self._adapters:
            raise ValueError(f"Unknown default agent: {self._default_agent_id}")

    @property
    def default_agent_id(self) -> str:
        """Return the configured default adapter identifier."""

        return self._default_agent_id

    def adapters(self) -> tuple[AgentAdapter, ...]:
        """Return registered adapters in deterministic insertion order."""

        return tuple(self._adapters.values())

    def descriptors(self) -> tuple[AgentDescriptor, ...]:
        """Return public metadata for registered adapters."""

        return tuple(adapter.descriptor for adapter in self._adapters.values())

    def get(self, agent_id: str) -> AgentAdapter:
        """Resolve an adapter or raise a useful error."""

        try:
            return self._adapters[agent_id]
        except KeyError as exc:
            raise KeyError(f"Unknown agent: {agent_id}") from exc


class AgentHarness:
    """Route provider-neutral use cases to the currently selected adapter."""

    def __init__(self, registry: AgentRegistry, store: StateStore | None = None) -> None:
        self.registry = registry
        self.store = store or ChatStateStore()

    def state_for(self, conversation_id: ConversationId) -> ChatState:
        """Return state initialized with the registry default agent."""

        return self.store.state_for(conversation_id, default_agent_id=self.registry.default_agent_id)

    def active_agent(self, conversation_id: ConversationId) -> AgentAdapter:
        """Return the adapter selected for one conversation."""

        return self.registry.get(self.state_for(conversation_id).active_agent_id)

    def active_descriptor(self, conversation_id: ConversationId) -> AgentDescriptor:
        """Return display metadata for one conversation's active adapter."""

        return self.active_agent(conversation_id).descriptor

    def select_agent(self, conversation_id: ConversationId, agent_id: str) -> AgentDescriptor:
        """Switch adapters and clear provider-specific state safely."""

        adapter = self.registry.get(agent_id)
        self.state_for(conversation_id).active_agent_id = agent_id
        self.store.reset_session(conversation_id)
        return adapter.descriptor

    async def ask(self, conversation_id: ConversationId, prompt: str) -> AgentReply:
        """Serialize a prompt and route it to the active agent adapter."""

        state = self.state_for(conversation_id)
        async with self.store.lock_for(conversation_id):
            adapter = self.active_agent(conversation_id)
            LOGGER.info(
                "agent_request_start conversation_id=%s agent_id=%s has_session=%s",
                conversation_id,
                adapter.descriptor.agent_id,
                bool(state.session_name),
            )
            reply = await adapter.ask(conversation_id, prompt, state=state)
            LOGGER.info(
                "agent_request_complete conversation_id=%s agent_id=%s response_chars=%s",
                conversation_id,
                adapter.descriptor.agent_id,
                len(reply.text),
            )
            return reply

    async def list_sources(self, conversation_id: ConversationId) -> list[AgentSource]:
        """List sources from the active adapter."""

        return await self.active_agent(conversation_id).list_sources()

    async def get_session(self, conversation_id: ConversationId, session_name: str) -> AgentSession:
        """Retrieve one provider session through the active adapter."""

        return await self.active_agent(conversation_id).get_session(session_name)

    async def list_activities(
        self, conversation_id: ConversationId, session_name: str
    ) -> list[dict[str, Any]]:
        """Retrieve provider activity records for one session."""

        return await self.active_agent(conversation_id).list_activities(session_name)

    async def list_sessions(self, conversation_id: ConversationId) -> list[AgentSession]:
        """List visible sessions through the active adapter."""

        return await self.active_agent(conversation_id).list_sessions()

    async def approve_plan(self, conversation_id: ConversationId, session_name: str) -> None:
        """Approve an active provider plan."""

        await self.active_agent(conversation_id).approve_plan(session_name)

    async def delete_session(self, conversation_id: ConversationId, session_name: str) -> None:
        """Delete or close a provider session."""

        await self.active_agent(conversation_id).delete_session(session_name)

    async def close(self) -> None:
        """Close every registered adapter exactly once."""

        await asyncio.gather(*(adapter.close() for adapter in self.registry.adapters()))
