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
)

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ChatState:
    """Conversation state that can later be persisted by another store."""

    active_agent_id: str = ""
    session_name: str | None = None
    selected_source: AgentSource | None = None
    selected_branch: str | None = None
    source_options: dict[str, AgentSource] = field(default_factory=dict)
    branch_options: dict[str, str] = field(default_factory=dict)
    session_options: dict[str, str] = field(default_factory=dict)
    agent_options: dict[str, str] = field(default_factory=dict)


class StateStore(Protocol):
    """Port for process-local or durable chat state implementations."""

    def state_for(self, chat_id: int, *, default_agent_id: str = "") -> ChatState:
        """Return the state associated with a chat."""
        ...

    def lock_for(self, chat_id: int) -> asyncio.Lock:
        """Return the lock used to serialize operations for a chat."""
        ...

    def reset_session(self, chat_id: int) -> None:
        """Clear the remote session association while preserving agent choice."""
        ...


class ChatStateStore:
    """Small in-memory state-store implementation for one process."""

    def __init__(self) -> None:
        self._states: dict[int, ChatState] = {}
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def state_for(self, chat_id: int, *, default_agent_id: str = "") -> ChatState:
        """Return or initialize the state for a Telegram chat."""

        state = self._states.setdefault(chat_id, ChatState(active_agent_id=default_agent_id))
        if not state.active_agent_id:
            state.active_agent_id = default_agent_id
        return state

    def lock_for(self, chat_id: int) -> asyncio.Lock:
        """Return the per-chat lock used to serialize agent operations."""

        return self._locks[chat_id]

    def reset_session(self, chat_id: int) -> None:
        """Clear the active remote session while preserving agent selection."""

        state = self.state_for(chat_id)
        state.session_name = None
        state.source_options.clear()
        state.branch_options.clear()
        state.session_options.clear()

    def reset(self, chat_id: int) -> None:
        """Clear all process-local state for a chat."""

        self._states.pop(chat_id, None)
        self._locks.pop(chat_id, None)


class AgentRegistry:
    """Resolve registered agent adapters by stable ID."""

    def __init__(self, adapters: Iterable[AgentAdapter], *, default_agent_id: str | None = None) -> None:
        adapter_list = list(adapters)
        self._adapters: dict[str, AgentAdapter] = {}
        for adapter in adapter_list:
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
        """Return the configured default agent ID."""

        return self._default_agent_id

    def adapters(self) -> tuple[AgentAdapter, ...]:
        """Return registered adapters in deterministic order."""

        return tuple(self._adapters.values())

    def descriptors(self) -> tuple[AgentDescriptor, ...]:
        """Return registered descriptors in deterministic order."""

        return tuple(adapter.descriptor for adapter in self._adapters.values())

    def get(self, agent_id: str) -> AgentAdapter:
        """Resolve an adapter or raise a useful error."""

        try:
            return self._adapters[agent_id]
        except KeyError as exc:
            raise KeyError(f"Unknown agent: {agent_id}") from exc


class AgentHarness:
    """Route provider-neutral use cases to the selected agent adapter."""

    def __init__(self, registry: AgentRegistry, store: StateStore | None = None) -> None:
        self.registry = registry
        self.store = store or ChatStateStore()

    def state_for(self, chat_id: int) -> ChatState:
        """Return state initialized with the default registered agent."""

        return self.store.state_for(chat_id, default_agent_id=self.registry.default_agent_id)

    def active_agent(self, chat_id: int) -> AgentAdapter:
        """Return the adapter selected for one chat."""

        state = self.state_for(chat_id)
        return self.registry.get(state.active_agent_id)

    def active_descriptor(self, chat_id: int) -> AgentDescriptor:
        """Return display metadata for the active chat agent."""

        return self.active_agent(chat_id).descriptor

    def select_agent(self, chat_id: int, agent_id: str) -> AgentDescriptor:
        """Switch agents and clear provider-specific session state."""

        adapter = self.registry.get(agent_id)
        state = self.state_for(chat_id)
        state.active_agent_id = agent_id
        self.store.reset_session(chat_id)
        return adapter.descriptor

    async def ask(self, chat_id: int, prompt: str) -> AgentReply:
        """Serialize a chat request and route it to the active adapter."""

        state = self.state_for(chat_id)
        async with self.store.lock_for(chat_id):
            adapter = self.active_agent(chat_id)
            LOGGER.info(
                "agent_request_start chat_id=%s agent_id=%s has_session=%s",
                chat_id,
                adapter.descriptor.agent_id,
                bool(state.session_name),
            )
            reply = await adapter.ask(chat_id, prompt, state=state)
            LOGGER.info(
                "agent_request_complete chat_id=%s agent_id=%s response_chars=%s",
                chat_id,
                adapter.descriptor.agent_id,
                len(reply.text),
            )
            return reply

    async def list_sources(self, chat_id: int) -> list[AgentSource]:
        """List sources from the active agent."""

        return await self.active_agent(chat_id).list_sources()

    async def get_session(self, chat_id: int, session_name: str) -> AgentSession:
        """Retrieve a session from the active agent."""

        return await self.active_agent(chat_id).get_session(session_name)

    async def list_activities(self, chat_id: int, session_name: str) -> list[dict[str, Any]]:
        """Retrieve activities from the active agent."""

        return await self.active_agent(chat_id).list_activities(session_name)

    async def list_sessions(self, chat_id: int) -> list[AgentSession]:
        """List sessions from the active agent."""

        return await self.active_agent(chat_id).list_sessions()

    async def approve_plan(self, chat_id: int, session_name: str) -> None:
        """Approve a plan through the active agent."""

        await self.active_agent(chat_id).approve_plan(session_name)

    async def delete_session(self, chat_id: int, session_name: str) -> None:
        """Delete a session through the active agent."""

        await self.active_agent(chat_id).delete_session(session_name)

    async def close(self) -> None:
        """Close every registered adapter exactly once."""

        await asyncio.gather(*(adapter.close() for adapter in self.registry.adapters()))
