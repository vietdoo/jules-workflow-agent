"""Telegram commands, callbacks, and message forwarding for the Jules agent UI."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from html import escape
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from src.api.jules_client import (
    JulesAPIError,
    JulesClient,
    JulesClientError,
    JulesSession,
    JulesSource,
)
from src.config import Settings


LOGGER = logging.getLogger(__name__)
MAX_TELEGRAM_MESSAGE_LENGTH = 4096
MAX_SOURCE_BUTTONS = 30
MAX_SESSION_BUTTONS = 12


@dataclass(slots=True)
class ChatState:
    """In-memory UI state associated with one Telegram chat."""

    session_name: str | None = None
    selected_source: JulesSource | None = None
    selected_branch: str | None = None
    source_options: dict[str, JulesSource] = field(default_factory=dict)
    branch_options: dict[str, str] = field(default_factory=dict)
    session_options: dict[str, str] = field(default_factory=dict)


class ChatSessionStore:
    """Keep per-chat Jules context and serialize requests within each chat."""

    def __init__(self) -> None:
        self._states: dict[int, ChatState] = {}
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def state_for(self, chat_id: int) -> ChatState:
        """Return or create the UI state for a chat."""

        if chat_id not in self._states:
            self._states[chat_id] = ChatState()
        return self._states[chat_id]

    def clear_session(self, chat_id: int) -> None:
        """Clear only the active Jules session while preserving repository selection."""

        self.state_for(chat_id).session_name = None

    def reset(self, chat_id: int) -> None:
        """Clear all state for a chat."""

        self._states.pop(chat_id, None)

    def lock_for(self, chat_id: int) -> asyncio.Lock:
        """Return the lock used to serialize requests for one chat."""

        return self._locks[chat_id]


SESSION_STORE = ChatSessionStore()


def _client(context: ContextTypes.DEFAULT_TYPE) -> JulesClient | None:
    """Return the configured Jules client from application data."""

    value = context.application.bot_data.get("jules_client")
    return value if isinstance(value, JulesClient) else None


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    """Return validated settings from application data."""

    return context.application.bot_data["settings"]


def _home_markup() -> InlineKeyboardMarkup:
    """Build the primary navigation keyboard."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("New session", callback_data="session:new"),
                InlineKeyboardButton("Session status", callback_data="session:status"),
            ],
            [
                InlineKeyboardButton("Repositories", callback_data="source:list"),
                InlineKeyboardButton("Activities", callback_data="session:activities"),
            ],
            [
                InlineKeyboardButton("All sessions", callback_data="session:list"),
                InlineKeyboardButton("Help", callback_data="ui:help"),
            ],
        ]
    )


def _session_markup(session: JulesSession) -> InlineKeyboardMarkup:
    """Build contextual controls for one Jules session."""

    rows = [
        [
            InlineKeyboardButton("Refresh status", callback_data="session:status"),
            InlineKeyboardButton("Activities", callback_data="session:activities"),
        ]
    ]
    state = (session.state or "").upper()
    if "PLAN" in state or session.require_plan_approval is True:
        rows.append(
            [InlineKeyboardButton("Approve plan", callback_data="session:approve")]
        )
    if session.url:
        rows.append([InlineKeyboardButton("Open in Jules", url=session.url)])
    rows.extend(
        [
            [
                InlineKeyboardButton("End session", callback_data="session:end"),
                InlineKeyboardButton("New session", callback_data="session:new"),
            ],
            [InlineKeyboardButton("Back to menu", callback_data="ui:home")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def _escape(value: Any) -> str:
    """Escape a value for Telegram HTML parse mode."""

    return escape(str(value))


def _format_session(session: JulesSession, state: ChatState) -> str:
    """Render a compact, polished session status card."""

    lines = [
        "<b>Jules session</b>",
        "",
        f"<b>State:</b> <code>{_escape(session.state_label)}</code>",
        f"<b>Title:</b> {_escape(session.title or 'Untitled task')}",
        f"<b>ID:</b> <code>{_escape(session.identifier)}</code>",
    ]
    source = session.source_name or (state.selected_source.name if state.selected_source else None)
    branch = session.starting_branch or state.selected_branch
    if source:
        lines.append(f"<b>Source:</b> <code>{_escape(source)}</code>")
    if branch:
        lines.append(f"<b>Branch:</b> <code>{_escape(branch)}</code>")
    if session.url:
        lines.append(f"<b>Web:</b> <a href=\"{_escape(session.url)}\">Open Jules session</a>")
    return "\n".join(lines)


def _activity_kind(activity: dict[str, Any]) -> tuple[str, str]:
    """Return a readable activity kind and detail string."""

    if isinstance(activity.get("agentMessaged"), dict):
        message = activity["agentMessaged"].get("agentMessage", "")
        return "Agent", str(message)
    if isinstance(activity.get("userMessaged"), dict):
        message = activity["userMessaged"].get("userMessage", "")
        return "You", str(message)
    if isinstance(activity.get("planGenerated"), dict):
        plan = activity["planGenerated"].get("plan", {})
        steps = plan.get("steps", []) if isinstance(plan, dict) else []
        titles = [
            str(step.get("title"))
            for step in steps[:4]
            if isinstance(step, dict) and step.get("title")
        ]
        return "Plan", "; ".join(titles) or "A plan was generated."
    if isinstance(activity.get("planApproved"), dict):
        return "Plan", "Plan approved."
    if isinstance(activity.get("progressUpdated"), dict):
        progress = activity["progressUpdated"]
        title = progress.get("title", "Progress update")
        description = progress.get("description", "")
        return "Progress", f"{title}: {description}".strip(": ")
    if isinstance(activity.get("sessionCompleted"), dict):
        return "Complete", "Session completed."
    if isinstance(activity.get("sessionFailed"), dict):
        return "Failed", str(activity["sessionFailed"].get("reason", "Session failed."))
    if activity.get("artifacts"):
        return "Artifact", f"{len(activity['artifacts'])} artifact(s) produced."
    return "System", str(activity.get("description", "Session activity."))


def _format_activities(activities: list[dict[str, Any]]) -> str:
    """Render the most recent activities as a readable timeline."""

    if not activities:
        return "<b>Activity timeline</b>\n\nNo activities have been reported yet."
    lines = ["<b>Activity timeline</b>", ""]
    for activity in activities[-10:]:
        kind, detail = _activity_kind(activity)
        timestamp = str(activity.get("createTime", ""))
        if "T" in timestamp:
            timestamp = timestamp.split("T", 1)[-1].replace("Z", "")[:8]
        detail = detail.strip() or "No details available."
        if len(detail) > 350:
            detail = f"{detail[:347]}..."
        lines.append(f"<b>{_escape(timestamp)} · {_escape(kind)}</b>")
        lines.append(_escape(detail))
        if activity.get("artifacts"):
            lines.append(f"<i>Artifacts: {len(activity['artifacts'])}</i>")
        lines.append("")
    return "\n".join(lines).strip()


async def _edit_query(
    query: Any,
    text: str,
    *,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit the callback's message while tolerating Telegram's no-op error."""

    try:
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def _show_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the primary agent navigation card."""

    chat_id = update.effective_chat.id if update.effective_chat else None
    state = SESSION_STORE.state_for(chat_id) if chat_id is not None else ChatState()
    selected = state.selected_source.label if state.selected_source else "API default"
    text = (
        "<b>Jules Workflow Agent</b>\n\n"
        "Send a coding task as a normal message, or use the controls below to "
        "choose a repository, inspect progress, approve plans, and manage sessions.\n\n"
        f"<b>Selected repository:</b> {_escape(selected)}"
    )
    if update.callback_query:
        await _edit_query(update.callback_query, text, markup=_home_markup())
    elif update.effective_message:
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_home_markup(),
            disable_web_page_preview=True,
        )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the welcome and navigation card for /start."""

    await _show_home(update, context)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Explain commands and interactive controls."""

    text = (
        "<b>Jules Workflow Agent help</b>\n\n"
        "Send any ordinary text message to create or continue a Jules coding session.\n\n"
        "<b>Commands</b>\n"
        "/start — open the agent menu\n"
        "/help — show this guide\n"
        "/sources — browse connected repositories\n"
        "/session — inspect the active session\n"
        "/activities — view the activity timeline\n"
        "/sessions — list recent Jules sessions\n"
        "/new — start a new conversation\n\n"
        "The API can read connected sources, but repository connection itself must be "
        "completed in the Jules web app."
    )
    if update.callback_query:
        await _edit_query(update.callback_query, text, markup=_home_markup())
    elif update.effective_message:
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_home_markup(),
        )


async def sources_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List connected Jules repositories and expose source selection buttons."""

    client = _client(context)
    chat_id = update.effective_chat.id if update.effective_chat else None
    if client is None or chat_id is None:
        return
    try:
        sources = await client.list_sources()
    except JulesClientError as exc:
        await _send_error(update, f"Could not load repositories: {exc}")
        return
    state = SESSION_STORE.state_for(chat_id)
    state.source_options = {str(index): source for index, source in enumerate(sources[:MAX_SOURCE_BUTTONS])}
    if not sources:
        text = "<b>Connected repositories</b>\n\nNo Jules sources are available yet. Connect a GitHub repository in Jules first."
        markup = _home_markup()
    else:
        text = (
            "<b>Connected repositories</b>\n\n"
            "Choose a repository for the next new session. Existing sessions are unchanged."
        )
        buttons = [
            [InlineKeyboardButton(source.label, callback_data=f"source:pick:{index}")]
            for index, source in state.source_options.items()
        ]
        buttons.append([InlineKeyboardButton("Back to menu", callback_data="ui:home")])
        markup = InlineKeyboardMarkup(buttons)
    await _respond(update, text, markup=markup)


async def session_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the current Jules session status."""

    client = _client(context)
    chat_id = update.effective_chat.id if update.effective_chat else None
    if client is None or chat_id is None:
        return
    state = SESSION_STORE.state_for(chat_id)
    if not state.session_name:
        await _respond(
            update,
            "<b>No active session</b>\n\nSend a task to Jules or choose a repository to begin.",
            markup=_home_markup(),
        )
        return
    try:
        session = await client.get_session(state.session_name)
    except JulesClientError as exc:
        await _send_error(update, f"Could not load the session: {exc}")
        return
    await _respond(update, _format_session(session, state), markup=_session_markup(session))


async def activities_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the latest activity timeline for the active session."""

    client = _client(context)
    chat_id = update.effective_chat.id if update.effective_chat else None
    if client is None or chat_id is None:
        return
    state = SESSION_STORE.state_for(chat_id)
    if not state.session_name:
        await _respond(update, "<b>No active session</b>\n\nThere are no activities to display.", markup=_home_markup())
        return
    try:
        activities = await client.list_activities(state.session_name, page_size=100)
    except JulesClientError as exc:
        await _send_error(update, f"Could not load activities: {exc}")
        return
    await _respond(
        update,
        _format_activities(activities),
        markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Refresh", callback_data="session:activities")],
                [InlineKeyboardButton("Session status", callback_data="session:status")],
            ]
        ),
    )


async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List recent sessions and allow the user to open one in the bot."""

    client = _client(context)
    chat_id = update.effective_chat.id if update.effective_chat else None
    if client is None or chat_id is None:
        return
    try:
        sessions = await client.list_sessions(page_size=50)
    except JulesClientError as exc:
        await _send_error(update, f"Could not load sessions: {exc}")
        return
    state = SESSION_STORE.state_for(chat_id)
    state.session_options = {
        str(index): session.name for index, session in enumerate(sessions[:MAX_SESSION_BUTTONS])
    }
    if not sessions:
        await _respond(update, "<b>Jules sessions</b>\n\nNo sessions found.", markup=_home_markup())
        return
    lines = ["<b>Jules sessions</b>", "", "Choose a session to open it in this chat:"]
    buttons: list[list[InlineKeyboardButton]] = []
    for index, session in enumerate(sessions[:MAX_SESSION_BUTTONS]):
        lines.append(f"{index + 1}. {_escape(session.title or session.identifier)} · {_escape(session.state_label)}")
        buttons.append(
            [InlineKeyboardButton(
                f"{index + 1}. {session.title or session.identifier}"[:60],
                callback_data=f"session:open:{index}",
            )]
        )
    buttons.append([InlineKeyboardButton("Back to menu", callback_data="ui:home")])
    await _respond(update, "\n".join(lines), markup=InlineKeyboardMarkup(buttons))


async def new_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask for confirmation before clearing the active chat session."""

    await _respond(
        update,
        "<b>Start a new session?</b>\n\nThis clears only the chat's local session link. It does not delete the Jules session.",
        markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Confirm new session", callback_data="session:new:confirm"),
                    InlineKeyboardButton("Cancel", callback_data="ui:home"),
                ]
            ]
        ),
    )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward a text message to Jules and return the agent response."""

    message = update.effective_message
    chat = update.effective_chat
    client = _client(context)
    if message is None or chat is None or not message.text:
        return
    if client is None:
        await message.reply_text("The bot is still starting. Please try again shortly.")
        return

    prompt = message.text.strip()
    if not prompt:
        await message.reply_text("Please send a non-empty text message.")
        return

    chat_id = chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    async with SESSION_STORE.lock_for(chat_id):
        try:
            response = await _ask_jules(client, context, chat_id, prompt)
        except JulesClientError as exc:
            LOGGER.warning("Jules request failed for chat %s: %s", chat_id, exc)
            await message.reply_text(
                "Jules could not complete that request right now. "
                "Use Session status or Activities to inspect the current task."
            )
            return
        except Exception:
            LOGGER.exception("Unexpected message handling failure for chat %s", chat_id)
            await message.reply_text("An unexpected error occurred. Please try again later.")
            return

    await _reply_in_chunks(message, response, markup=_home_markup())


async def _ask_jules(
    client: JulesClient,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    prompt: str,
) -> str:
    """Continue the active session or create a fully configured new session."""

    state = SESSION_STORE.state_for(chat_id)
    if state.session_name:
        try:
            return await client.continue_session(state.session_name, prompt)
        except JulesAPIError as exc:
            if exc.status != 404:
                raise
            LOGGER.info("Jules session %s no longer exists; creating a new one.", state.session_name)
            state.session_name = None

    settings = _settings(context)
    source_name = state.selected_source.name if state.selected_source else settings.jules_source
    branch = state.selected_branch or settings.jules_starting_branch
    session = await client.create_session(
        prompt,
        title=f"Telegram chat {chat_id}",
        source_name=source_name,
        starting_branch=branch,
        require_plan_approval=settings.jules_require_plan_approval,
        automation_mode=settings.jules_automation_mode,
    )
    state.session_name = session.name
    return await client.wait_for_agent_reply(session.name)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch inline-keyboard actions to Jules API operations."""

    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data or ""
    try:
        if data == "ui:home":
            await _show_home(update, context)
        elif data == "ui:help":
            await help_command(update, context)
        elif data == "source:list":
            await sources_command(update, context)
        elif data.startswith("source:pick:"):
            await _select_source(update, context, data.rsplit(":", 1)[-1])
        elif data.startswith("source:branch:"):
            await _select_branch(update, context, data.rsplit(":", 1)[-1])
        elif data == "session:status":
            await session_command(update, context)
        elif data == "session:activities":
            await activities_command(update, context)
        elif data == "session:list":
            await sessions_command(update, context)
        elif data.startswith("session:open:"):
            await _open_session(update, context, data.rsplit(":", 1)[-1])
        elif data == "session:approve":
            await _confirm_approval(update)
        elif data == "session:approve:confirm":
            await _approve_session(update, context)
        elif data == "session:end":
            await _confirm_end(update)
        elif data == "session:end:confirm":
            await _end_session(update, context)
        elif data == "session:new":
            await new_session_command(update, context)
        elif data == "session:new:confirm":
            chat_id = update.effective_chat.id if update.effective_chat else None
            if chat_id is not None:
                SESSION_STORE.clear_session(chat_id)
            await _show_home(update, context)
    except JulesClientError as exc:
        LOGGER.warning("Jules callback failed: %s", exc)
        await query.answer("Jules request failed. Please try again.", show_alert=True)
    except Exception:
        LOGGER.exception("Unexpected callback failure for %s", data)
        await query.answer("Something went wrong. Please try again.", show_alert=True)


async def _select_source(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    """Store a selected repository and present its available branches."""

    query = update.callback_query
    chat_id = update.effective_chat.id if update.effective_chat else None
    if query is None or chat_id is None:
        return
    state = SESSION_STORE.state_for(chat_id)
    source = state.source_options.get(key)
    if source is None:
        await query.answer("This repository list has expired. Open Repositories again.", show_alert=True)
        return
    state.selected_source = source
    branches = list(source.branches) or ([source.default_branch] if source.default_branch else ["main"])
    state.branch_options = {str(index): branch for index, branch in enumerate(branches)}
    buttons = [
        [InlineKeyboardButton(branch, callback_data=f"source:branch:{index}")]
        for index, branch in state.branch_options.items()
    ]
    buttons.append([InlineKeyboardButton("Back to repositories", callback_data="source:list")])
    text = (
        f"<b>{_escape(source.label)}</b>\n\n"
        "Choose the starting branch for the next session."
    )
    await _edit_query(query, text, markup=InlineKeyboardMarkup(buttons))


async def _select_branch(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    """Store a selected branch and return to the main menu."""

    query = update.callback_query
    chat_id = update.effective_chat.id if update.effective_chat else None
    if query is None or chat_id is None:
        return
    state = SESSION_STORE.state_for(chat_id)
    branch = state.branch_options.get(key)
    if branch is None or state.selected_source is None:
        await query.answer("This branch selection has expired. Open Repositories again.", show_alert=True)
        return
    state.selected_branch = branch
    await _edit_query(
        query,
        f"<b>Repository selected</b>\n\n<b>Repository:</b> {_escape(state.selected_source.label)}\n<b>Branch:</b> <code>{_escape(branch)}</code>\n\nYour next new task will use this context.",
        markup=_home_markup(),
    )


async def _open_session(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    """Attach a selected remote Jules session to the current chat."""

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    state = SESSION_STORE.state_for(chat_id)
    session_name = state.session_options.get(key)
    if not session_name:
        await _send_error(update, "This session list has expired. Open All sessions again.")
        return
    state.session_name = session_name
    await session_command(update, context)


async def _confirm_approval(update: Update) -> None:
    """Ask for confirmation before approving a Jules plan."""

    await _respond(
        update,
        "<b>Approve this plan?</b>\n\nJules will continue executing the current session plan.",
        markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Approve plan", callback_data="session:approve:confirm"),
                    InlineKeyboardButton("Cancel", callback_data="session:status"),
                ]
            ]
        ),
    )


async def _approve_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Approve the active session plan and refresh the status card."""

    chat_id = update.effective_chat.id if update.effective_chat else None
    client = _client(context)
    if chat_id is None or client is None:
        return
    session_name = SESSION_STORE.state_for(chat_id).session_name
    if not session_name:
        await _send_error(update, "There is no active session to approve.")
        return
    await client.approve_plan(session_name)
    await session_command(update, context)


async def _confirm_end(update: Update) -> None:
    """Ask for confirmation before deleting a Jules session."""

    await _respond(
        update,
        "<b>End and delete this Jules session?</b>\n\nThis calls the Jules DELETE endpoint and cannot be undone from the bot.",
        markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Delete session", callback_data="session:end:confirm"),
                    InlineKeyboardButton("Cancel", callback_data="session:status"),
                ]
            ]
        ),
    )


async def _end_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete the active Jules session and clear the chat association."""

    chat_id = update.effective_chat.id if update.effective_chat else None
    client = _client(context)
    if chat_id is None or client is None:
        return
    state = SESSION_STORE.state_for(chat_id)
    if state.session_name:
        try:
            await client.delete_session(state.session_name)
        except JulesAPIError as exc:
            if exc.status != 404:
                raise
        state.session_name = None
    await _show_home(update, context)


async def _respond(
    update: Update,
    text: str,
    *,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Reply to a command or edit a callback message with HTML formatting."""

    if update.callback_query:
        await _edit_query(update.callback_query, text, markup=markup)
    elif update.effective_message:
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )


async def _send_error(update: Update, detail: str) -> None:
    """Send a concise, safe error card without exposing credentials."""

    safe_detail = detail.replace("X-Goog-Api-Key", "API key")
    await _respond(
        update,
        f"<b>Jules request failed</b>\n\n{_escape(safe_detail)}",
        markup=_home_markup(),
    )


async def _reply_in_chunks(
    message: Any,
    text: str,
    *,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Send agent text while respecting Telegram's message-size limit."""

    clean_text = text.strip() or "Jules returned an empty response."
    chunks = [
        clean_text[start : start + MAX_TELEGRAM_MESSAGE_LENGTH]
        for start in range(0, len(clean_text), MAX_TELEGRAM_MESSAGE_LENGTH)
    ]
    for index, chunk in enumerate(chunks):
        await message.reply_text(chunk, reply_markup=markup if index == len(chunks) - 1 else None)


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
