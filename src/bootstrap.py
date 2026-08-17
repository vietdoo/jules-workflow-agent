"""Application composition helpers shared by Telegram and the web control plane."""

from __future__ import annotations

from src.api.jules_client import JulesClient
from src.application.harness import AgentHarness, AgentRegistry, StateStore
from src.config import ConfigurationError, Settings
from src.infrastructure.agents.jules_agent import JulesAgent


def build_agent_harness(settings: Settings, *, store: StateStore | None = None) -> AgentHarness:
    """Compose the configured provider adapters into one agent harness."""

    jules_client = JulesClient(
        api_key=settings.jules_api_key,
        base_url=settings.jules_api_url,
        request_timeout_seconds=settings.jules_timeout_seconds,
        poll_interval_seconds=settings.jules_poll_interval_seconds,
        reply_timeout_seconds=settings.jules_reply_timeout_seconds,
    )
    jules_agent = JulesAgent(jules_client, settings)
    try:
        registry = AgentRegistry([jules_agent], default_agent_id=settings.agent_default_id)
    except ValueError as exc:
        raise ConfigurationError(f"Invalid agent configuration: {exc}") from exc
    return AgentHarness(registry, store=store)
