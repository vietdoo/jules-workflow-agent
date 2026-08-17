"""Application entrypoint for the Telegram bot."""

from __future__ import annotations

import logging
import sys

from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters

from src.api.jules_client import JulesClient
from src.config import ConfigurationError, Settings, get_settings
from src.handlers.message_handlers import (
    error_handler,
    help_command,
    message_handler,
    start_command,
)


LOGGER = logging.getLogger(__name__)
WEBHOOK_PATH = "/telegram/webhook"


async def _close_client(application: Application) -> None:
    """Close the external API client during Telegram application shutdown."""

    client = application.bot_data.get("jules_client")
    if isinstance(client, JulesClient):
        await client.close()


def build_application(settings: Settings) -> Application:
    """Build and configure the Telegram application instance."""

    jules_client = JulesClient(
        api_key=settings.jules_api_key,
        base_url=settings.jules_api_url,
        request_timeout_seconds=settings.jules_timeout_seconds,
        poll_interval_seconds=settings.jules_poll_interval_seconds,
        reply_timeout_seconds=settings.jules_reply_timeout_seconds,
    )
    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_shutdown(_close_client)
        .build()
    )
    application.bot_data["settings"] = settings
    application.bot_data["jules_client"] = jules_client

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    application.add_error_handler(error_handler)
    return application


def _webhook_url(settings: Settings) -> str:
    """Build the public Telegram webhook URL from the configured base URL."""

    assert settings.webhook_url is not None
    if settings.webhook_url.endswith(WEBHOOK_PATH):
        return settings.webhook_url
    return f"{settings.webhook_url}{WEBHOOK_PATH}"


def run(settings: Settings) -> None:
    """Start the bot in webhook mode when configured, otherwise use polling."""

    application = build_application(settings)
    common_options = {
        "allowed_updates": Update.ALL_TYPES,
        "drop_pending_updates": True,
    }

    if settings.use_webhook:
        public_webhook_url = _webhook_url(settings)
        LOGGER.info("Starting Telegram webhook listener on port %s", settings.port)
        LOGGER.info("Telegram webhook URL: %s", public_webhook_url)
        application.run_webhook(
            listen="0.0.0.0",
            port=settings.port,
            url_path=WEBHOOK_PATH.lstrip("/"),
            webhook_url=public_webhook_url,
            secret_token=settings.webhook_secret_token,
            **common_options,
        )
        return

    LOGGER.info("WEBHOOK_URL is not set; starting Telegram long polling.")
    application.run_polling(**common_options)


def main() -> None:
    """Load configuration and run the bot process."""

    try:
        settings = get_settings()
    except ConfigurationError as exc:
        logging.basicConfig(level=logging.ERROR)
        LOGGER.error("Configuration error: %s", exc)
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    run(settings)


if __name__ == "__main__":
    main()
