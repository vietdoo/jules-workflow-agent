"""Jules adapter with durable session assignment before asynchronous activity polling."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.api.jules_client import JulesAPIError, JulesClient, JulesSession, JulesSource
from src.config import Settings
from src.domain.agent import AgentDescriptor, AgentReply, AgentSession, AgentSource, ConversationId


def _source_view(source: JulesSource) -> AgentSource:
    """Convert a Jules source into the normalized domain representation."""

    return AgentSource(
        name=source.name,
        label=source.label,
        default_branch=source.default_branch,
        branches=tuple(source.branches),
        metadata={"provider": "jules"},
    )


def _session_view(session: JulesSession) -> AgentSession:
    """Convert a Jules session into the normalized domain representation."""

    return AgentSession(
        name=session.name,
        identifier=session.identifier,
        title=session.title,
        state=session.state,
        state_label=session.state_label,
        url=session.url,
        source_name=session.source_name,
        starting_branch=session.starting_branch,
        require_plan_approval=session.require_plan_approval,
        metadata={"provider": "jules"},
    )


class JulesAgent:
    """Adapt the Jules REST client to the shared agent harness contract."""

    def __init__(
        self,
        client: JulesClient,
        settings: Settings,
        *,
        state_saver: Callable[[], None] | None = None,
    ) -> None:
        self.client = client
        self.settings = settings
        self._state_saver = state_saver
        self._descriptor = AgentDescriptor(
            agent_id="jules",
            display_name="Jules",
            description="Google's asynchronous coding agent for connected repositories.",
            icon="J",
            capabilities=("coding", "plans", "activities", "artifacts", "pull-requests"),
        )

    @property
    def descriptor(self) -> AgentDescriptor:
        """Return the Jules UI and routing metadata."""

        return self._descriptor

    async def ask(self, conversation_id: ConversationId, prompt: str, *, state: Any) -> AgentReply:
        """Continue an active session or create a configured Jules session."""

        if state.session_name:
            try:
                text = await self.client.continue_session(state.session_name, prompt)
                return AgentReply(text=text, agent_id=self.descriptor.agent_id)
            except JulesAPIError as exc:
                if exc.status != 404:
                    raise
                state.session_name = None
                if self._state_saver is not None:
                    self._state_saver()

        source_name = state.selected_source.name if state.selected_source else self.settings.jules_source
        branch = state.selected_branch or self.settings.jules_starting_branch
        session = await self.client.create_session(
            prompt,
            title=f"Workflow {conversation_id}",
            source_name=source_name,
            starting_branch=branch,
            require_plan_approval=self.settings.jules_require_plan_approval,
            automation_mode=self.settings.jules_automation_mode,
        )
        state.session_name = session.name
        # Persist before awaiting activity polling: session creation is immediate
        # but activity visibility is asynchronous, and the runner may restart.
        if self._state_saver is not None:
            self._state_saver()
        text = await self.client.wait_for_agent_reply(session.name)
        return AgentReply(text=text, session=_session_view(session), agent_id=self.descriptor.agent_id)

    async def list_sources(self) -> list[AgentSource]:
        """List connected Jules sources."""

        return [_source_view(source) for source in await self.client.list_sources()]

    async def get_session(self, session_name: str) -> AgentSession:
        """Retrieve a normalized Jules session."""

        return _session_view(await self.client.get_session(session_name))

    async def list_activities(self, session_name: str) -> list[dict[str, Any]]:
        """Retrieve all activities for a Jules session."""

        return await self.client.list_activities(session_name, page_size=100)

    async def list_sessions(self) -> list[AgentSession]:
        """List recent normalized Jules sessions."""

        return [_session_view(session) for session in await self.client.list_sessions(page_size=50)]

    async def approve_plan(self, session_name: str) -> None:
        """Approve the active Jules plan."""

        await self.client.approve_plan(session_name)

    async def delete_session(self, session_name: str) -> None:
        """Delete a Jules session, tolerating an already-deleted resource."""

        try:
            await self.client.delete_session(session_name)
        except JulesAPIError as exc:
            if exc.status != 404:
                raise

    async def close(self) -> None:
        """Close the underlying Jules HTTP client."""

        await self.client.close()
