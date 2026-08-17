"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from dotenv import load_dotenv


DEFAULT_JULES_API_URL: Final[str] = "https://jules.googleapis.com/v1alpha"
DEFAULT_PORT: Final[int] = 8080
DEFAULT_JULES_TIMEOUT_SECONDS: Final[float] = 60.0
DEFAULT_JULES_POLL_INTERVAL_SECONDS: Final[float] = 2.0
DEFAULT_JULES_REPLY_TIMEOUT_SECONDS: Final[float] = 120.0


class ConfigurationError(RuntimeError):
    """Raised when a required or invalid configuration value is detected."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable runtime settings for the bot process."""

    telegram_bot_token: str
    jules_api_key: str
    jules_api_url: str
    webhook_url: str | None
    port: int
    jules_timeout_seconds: float
    jules_poll_interval_seconds: float
    jules_reply_timeout_seconds: float
    log_level: str
    webhook_secret_token: str | None
    jules_source: str | None
    jules_starting_branch: str
    jules_require_plan_approval: bool
    jules_automation_mode: str | None
    agent_default_id: str

    @property
    def use_webhook(self) -> bool:
        """Return whether the process should start in webhook mode."""

        return bool(self.webhook_url)


def _required(name: str) -> str:
    """Read a non-empty required environment variable."""

    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(
            f"Missing required environment variable: {name}. "
            "See .env.example for the expected configuration."
        )
    return value


def _positive_float(name: str, default: float) -> float:
    """Read a positive floating-point environment variable."""

    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number, got {raw_value!r}.") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero.")
    return value


def _positive_int(name: str, default: int) -> int:
    """Read a positive integer environment variable."""

    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer, got {raw_value!r}.") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero.")
    return value


def _boolean(name: str, default: bool) -> bool:
    """Read a human-friendly boolean environment variable."""

    raw_value = os.getenv(name, str(default)).strip().lower()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(
        f"{name} must be one of true/false, yes/no, or 1/0; got {raw_value!r}."
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate settings once for the lifetime of the process."""

    load_dotenv()
    return Settings(
        telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
        jules_api_key=_required("JULES_API_KEY"),
        jules_api_url=os.getenv("JULES_API_URL", DEFAULT_JULES_API_URL).rstrip("/"),
        webhook_url=os.getenv("WEBHOOK_URL", "").strip().rstrip("/") or None,
        port=_positive_int("PORT", DEFAULT_PORT),
        jules_timeout_seconds=_positive_float(
            "JULES_TIMEOUT_SECONDS", DEFAULT_JULES_TIMEOUT_SECONDS
        ),
        jules_poll_interval_seconds=_positive_float(
            "JULES_POLL_INTERVAL_SECONDS", DEFAULT_JULES_POLL_INTERVAL_SECONDS
        ),
        jules_reply_timeout_seconds=_positive_float(
            "JULES_REPLY_TIMEOUT_SECONDS", DEFAULT_JULES_REPLY_TIMEOUT_SECONDS
        ),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        webhook_secret_token=os.getenv("WEBHOOK_SECRET_TOKEN", "").strip() or None,
        jules_source=os.getenv("JULES_SOURCE", "").strip() or None,
        jules_starting_branch=os.getenv("JULES_STARTING_BRANCH", "main").strip() or "main",
        jules_require_plan_approval=_boolean("JULES_REQUIRE_PLAN_APPROVAL", False),
        jules_automation_mode=os.getenv("JULES_AUTOMATION_MODE", "").strip() or None,
        agent_default_id=os.getenv("AGENT_DEFAULT_ID", "jules").strip() or "jules",
    )
