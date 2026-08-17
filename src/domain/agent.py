"""Provider-neutral contracts for the multi-agent application harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias


ConversationId: TypeAlias = int | str


@dataclass(frozen=True, slots=True)
class AgentDescriptor:
    """Public metadata used to render an agent in the Telegram UI."""

    agent_id: str
    display_name: str
    description: str
    icon: str = ""
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentSource:
    """Normalized repository or workspace source exposed by an agent."""

    name: str
    label: str
    default_branch: str | None = None
    branches: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentSession:
    """Normalized remote session metadata exposed by an agent."""

    name: str
    identifier: str
    title: str | None = None
    state: str | None = None
    state_label: str = "Unknown"
    url: str | None = None
    source_name: str | None = None
    starting_branch: str | None = None
    require_plan_approval: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentReply:
    """Normalized response returned by an agent adapter."""

    text: str
    session: AgentSession | None = None
    agent_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentAdapter(Protocol):
    """Protocol implemented by every agent provider adapter."""

    @property
    def descriptor(self) -> AgentDescriptor:
        """Return stable metadata for this provider."""
        ...

    async def ask(self, conversation_id: ConversationId, prompt: str, *, state: Any) -> AgentReply:
        """Create or continue work for one chat and return a normalized reply."""
        ...

    async def list_sources(self) -> list[AgentSource]:
        """List provider sources available for selection."""
        ...

    async def get_session(self, session_name: str) -> AgentSession:
        """Retrieve provider session metadata."""
        ...

    async def list_activities(self, session_name: str) -> list[dict[str, Any]]:
        """Retrieve provider activity records."""
        ...

    async def list_sessions(self) -> list[AgentSession]:
        """List provider sessions visible to the configured identity."""
        ...

    async def approve_plan(self, session_name: str) -> None:
        """Approve a pending provider plan."""
        ...

    async def delete_session(self, session_name: str) -> None:
        """Delete or close a provider session."""
        ...

    async def close(self) -> None:
        """Release provider resources."""
        ...
