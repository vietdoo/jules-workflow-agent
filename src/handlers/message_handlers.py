"""Telegram command and message handlers."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from telegram import Message, Update
from telegram.ext import ContextTypes

from src.api.jules_client import JulesAPIError, JulesClient, JulesClientError


LOGGER = logging.getLogger(__name__)
MAX_TELEGRAM_MESSAGE_LENGTH = 4096


class ChatSessionStore:
    """Keep one Jules session and one concurrency lock per Telegram chat."""

    def __init__(self) -> None:
        self._sessions: dict[int, str] = {}
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def get(self, chat_id: int) -> str | None:
        """Return the Jules session associated with a chat, if any."""

        return self._sessions.get(chat_id)

    def set(self, chat_id: int, session_name: str) -> None:
        """Associate a Jules session with a chat."""

        self._sessions[chat_id] = session_name

    def clear(self, chat_id: int) -> None:
        """Remove a chat's Jules session association."""

        self._sessions.pop(chat_id, None)

    def lock_for(self, chat_id: int) -> asyncio.Lock:
        """Return the lock used to serialize requests for one chat."""

        return self._locks[chat_id]


SESSION_STORE = ChatSessionStore()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message when a user invokes /start."""

    del context
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(
        "Welcome. Send me a message and I will forward it to Jules, then return "
        "the agent's response here. Use /help to see available commands."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Explain the available commands and conversation behavior."""

    del context
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(
        "Available commands:\n"
        "/start — show the welcome message\n"
        "/help — show this help message\n\n"
        "Any ordinary text message is sent to Jules. Messages in the same chat "
        "continue the same Jules session, so the agent can retain context. "
        "Restart the bot process to clear in-memory chat sessions."
    )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward a user's text message to Jules and return the asynchronous reply."""

    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.text:
        return

    client = context.application.bot_data.get("jules_client")
    if not isinstance(client, JulesClient):
        LOGGER.error("Jules client is not available in application context.")
        await message.reply_text("The bot is still starting. Please try again shortly.")
        return

    prompt = message.text.strip()
    if not prompt:
        await message.reply_text("Please send a non-empty text message.")
        return

    chat_id = chat.id
    await message.chat.send_action("typing")
    async with SESSION_STORE.lock_for(chat_id):
        try:
            response = await _ask_jules(client, chat_id, prompt)
        except JulesClientError as exc:
            LOGGER.warning("Jules request failed for chat %s: %s", chat_id, exc)
            await message.reply_text(
                "I could not get a response from Jules right now. "
                "Please try again in a moment."
            )
            return
        except Exception:
            LOGGER.exception("Unexpected message handling failure for chat %s", chat_id)
            await message.reply_text("An unexpected error occurred. Please try again later.")
            return

    await _reply_in_chunks(message, response)


async def _ask_jules(client: JulesClient, chat_id: int, prompt: str) -> str:
    """Continue a chat session or create one if this is the first message."""

    session_name = SESSION_STORE.get(chat_id)
    if session_name:
        try:
            return await client.continue_session(session_name, prompt)
        except JulesAPIError as exc:
            if exc.status != 404:
                raise
            LOGGER.info("Jules session %s no longer exists; creating a new one.", session_name)
            SESSION_STORE.clear(chat_id)

    session = await client.create_session(
        prompt,
        title=f"Telegram chat {chat_id}",
    )
    SESSION_STORE.set(chat_id, session.name)
    try:
        return await client.wait_for_agent_reply(session.name)
    except JulesClientError:
        # Keep the session so a retry can continue the same Jules task.
        raise


async def _reply_in_chunks(message: Message, text: str) -> None:
    """Send a response while respecting Telegram's message-size limit."""

    clean_text = text.strip() or "Jules returned an empty response."
    for start in range(0, len(clean_text), MAX_TELEGRAM_MESSAGE_LENGTH):
        await message.reply_text(clean_text[start : start + MAX_TELEGRAM_MESSAGE_LENGTH])


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log unhandled update errors and notify the user when possible."""

    LOGGER.error("Unhandled Telegram update error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(
                "Something went wrong while processing your request."
            )
        except Exception:
            LOGGER.exception("Could not send an error message to Telegram.")
