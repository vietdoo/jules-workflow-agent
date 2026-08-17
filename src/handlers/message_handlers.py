"""Telegram interface adapter for the multi-agent harness."""

from __future__ import annotations

import logging
from html import escape
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from src.api.jules_client import JulesClientError
from src.application.harness import AgentHarness, ChatState
from src.domain.agent import AgentSession

LOGGER = logging.getLogger(__name__)
MAX_TELEGRAM_MESSAGE_LENGTH = 4096
MAX_SOURCE_BUTTONS = 30
MAX_SESSION_BUTTONS = 12
MAX_AGENT_BUTTONS = 12


def _harness(context: ContextTypes.DEFAULT_TYPE) -> AgentHarness:
    """Return the application-level agent harness."""

    value = context.application.bot_data.get("agent_harness")
    if not isinstance(value, AgentHarness):
        raise RuntimeError("Agent harness is not initialized.")
    return value


def _escape(value: Any) -> str:
    """Escape a value for Telegram HTML parse mode."""

    return escape(str(value))


def _home_markup() -> InlineKeyboardMarkup:
    """Build the primary navigation keyboard."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("New task", callback_data="session:new"),
                InlineKeyboardButton("Status", callback_data="session:status"),
            ],
            [
                InlineKeyboardButton("Agents", callback_data="agent:list"),
                InlineKeyboardButton("Repositories", callback_data="source:list"),
            ],
            [
                InlineKeyboardButton("Activities", callback_data="session:activities"),
                InlineKeyboardButton("All sessions", callback_data="session:list"),
            ],
            [InlineKeyboardButton("Help", callback_data="ui:help")],
        ]
    )


def _agent_markup(harness: AgentHarness, state: ChatState) -> InlineKeyboardMarkup:
    """Build the agent picker keyboard using short opaque state keys."""

    descriptors = harness.registry.descriptors()[:MAX_AGENT_BUTTONS]
    state.agent_options = {str(index): descriptor.agent_id for index, descriptor in enumerate(descriptors)}
    rows = [
        [
            InlineKeyboardButton(
                f"{descriptor.icon} {descriptor.display_name}".strip(),
                callback_data=f"agent:pick:{index}",
            )
        ]
        for index, descriptor in enumerate(descriptors)
    ]
    rows.append([InlineKeyboardButton("Back to menu", callback_data="ui:home")])
    return InlineKeyboardMarkup(rows)


def _session_markup(session: AgentSession, agent_name: str) -> InlineKeyboardMarkup:
    """Build contextual controls for a provider session."""

    rows = [
        [
            InlineKeyboardButton("Refresh status", callback_data="session:status"),
            InlineKeyboardButton("Activities", callback_data="session:activities"),
        ]
    ]
    state = (session.state or "").upper()
    if "PLAN" in state or session.require_plan_approval is True:
        rows.append([InlineKeyboardButton("Approve plan", callback_data="session:approve")])
    if session.url:
        rows.append([InlineKeyboardButton(f"Open in {agent_name}", url=session.url)])
    rows.extend(
        [
            [
                InlineKeyboardButton("End session", callback_data="session:end"),
                InlineKeyboardButton("New task", callback_data="session:new"),
            ],
            [InlineKeyboardButton("Back to menu", callback_data="ui:home")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def _format_session(session: AgentSession, state: ChatState, agent_name: str) -> str:
    """Render a compact, professional session status card."""

    lines = [
        f"<b>{_escape(agent_name)} session</b>",
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
        lines.append(f"<b>Web:</b> <a href=\"{_escape(session.url)}\">Open session</a>")
    return "\n".join(lines)


def _activity_kind(activity: dict[str, Any]) -> tuple[str, str]:
    """Return a readable activity kind and detail string."""

    if isinstance(activity.get("agentMessaged"), dict):
        return "Agent", str(activity["agentMessaged"].get("agentMessage", ""))
    if isinstance(activity.get("userMessaged"), dict):
        return "You", str(activity["userMessaged"].get("userMessage", ""))
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
        return "Progress", f"{progress.get('title', 'Progress update')}: {progress.get('description', '')}".strip(": ")
    if isinstance(activity.get("sessionCompleted"), dict):
        return "Complete", "Session completed."
    if isinstance(activity.get("sessionFailed"), dict):
        return "Failed", str(activity["sessionFailed"].get("reason", "Session failed."))
    if activity.get("artifacts"):
        return "Artifact", f"{len(activity['artifacts'])} artifact(s) produced."
    return "System", str(activity.get("description", "Session activity."))


def _format_activities(activities: list[dict[str, Any]], agent_name: str) -> str:
    """Render recent activities as a compact timeline card."""

    if not activities:
        return f"<b>{_escape(agent_name)} activity timeline</b>\n\nNo activities have been reported yet."
    lines = [f"<b>{_escape(agent_name)} activity timeline</b>", ""]
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


async def _edit_query(query: Any, text: str, *, markup: InlineKeyboardMarkup | None = None) -> None:
    """Edit a callback message while tolerating Telegram no-op edits."""

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


async def _respond(update: Update, text: str, *, markup: InlineKeyboardMarkup | None = None) -> None:
    """Reply to a command or edit a callback message with safe HTML formatting."""

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
    """Send a concise error card without exposing credentials."""

    safe_detail = detail.replace("X-Goog-Api-Key", "API key")
    await _respond(
        update,
        f"<b>Request failed</b>\n\n{_escape(safe_detail)}",
        markup=_home_markup(),
    )


async def _show_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the primary agent navigation card."""

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    harness = _harness(context)
    state = harness.state_for(chat_id)
    descriptor = harness.active_descriptor(chat_id)
    selected = state.selected_source.label if state.selected_source else "API default"
    session_text = "Active session linked" if state.session_name else "No active session"
    text = (
        "<b>Jules Workflow Agent</b>\n\n"
        f"<b>Active agent:</b> {_escape(descriptor.display_name)}\n"
        f"<b>Repository:</b> {_escape(selected)}\n"
        f"<b>Conversation:</b> {_escape(session_text)}\n\n"
        "Send a task as a normal message, or use the controls below to choose an agent, "
        "select a repository, inspect progress, approve plans, and manage sessions."
    )
    await _respond(update, text, markup=_home_markup())


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the welcome and navigation card."""

    await _show_home(update, context)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Explain commands and interactive controls."""

    text = (
        "<b>Jules Workflow Agent help</b>\n\n"
        "Send ordinary text to create or continue work with the active agent.\n\n"
        "<b>Commands</b>\n"
        "/start — open the agent menu\n"
        "/help — show this guide\n"
        "/agents — choose the active agent\n"
        "/sources — browse connected repositories\n"
        "/session — inspect the active session\n"
        "/activities — view the activity timeline\n"
        "/sessions — list recent sessions\n"
        "/new — start a new conversation\n\n"
        "The harness keeps provider details behind adapters, so additional agents can be added without rewriting this Telegram interface."
    )
    await _respond(update, text, markup=_home_markup())


async def agents_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List registered agents and allow the user to switch providers."""

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    harness = _harness(context)
    current = harness.active_descriptor(chat_id)
    lines = ["<b>Agent workspace</b>", "", f"Active: <b>{_escape(current.display_name)}</b>", "", "Choose an agent:"]
    for descriptor in harness.registry.descriptors()[:MAX_AGENT_BUTTONS]:
        capabilities = ", ".join(descriptor.capabilities[:4]) or "general"
        lines.append(f"{descriptor.icon} <b>{_escape(descriptor.display_name)}</b> — {_escape(descriptor.description)} ({_escape(capabilities)})")
    await _respond(update, "\n".join(lines), markup=_agent_markup(harness, harness.state_for(chat_id)))


async def sources_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List connected repositories for the active agent."""

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    harness = _harness(context)
    try:
        sources = await harness.list_sources(chat_id)
    except JulesClientError as exc:
        await _send_error(update, f"Could not load repositories: {exc}")
        return
    state = harness.state_for(chat_id)
    state.source_options = {str(index): source for index, source in enumerate(sources[:MAX_SOURCE_BUTTONS])}
    if not sources:
        text = "<b>Connected repositories</b>\n\nNo sources are available yet. Connect a repository in the provider website first."
        markup = _home_markup()
    else:
        text = "<b>Connected repositories</b>\n\nChoose a repository for the next new session. Existing sessions are unchanged."
        buttons = [[InlineKeyboardButton(source.label, callback_data=f"source:pick:{index}")] for index, source in state.source_options.items()]
        buttons.append([InlineKeyboardButton("Back to menu", callback_data="ui:home")])
        markup = InlineKeyboardMarkup(buttons)
    await _respond(update, text, markup=markup)


async def session_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the active session status card."""

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    harness = _harness(context)
    state = harness.state_for(chat_id)
    if not state.session_name:
        await _respond(update, "<b>No active session</b>\n\nSend a task or choose a repository to begin.", markup=_home_markup())
        return
    try:
        session = await harness.get_session(chat_id, state.session_name)
    except JulesClientError as exc:
        await _send_error(update, f"Could not load the session: {exc}")
        return
    descriptor = harness.active_descriptor(chat_id)
    await _respond(update, _format_session(session, state, descriptor.display_name), markup=_session_markup(session, descriptor.display_name))


async def activities_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the latest activity timeline for the active session."""

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    harness = _harness(context)
    state = harness.state_for(chat_id)
    if not state.session_name:
        await _respond(update, "<b>No active session</b>\n\nThere are no activities to display.", markup=_home_markup())
        return
    try:
        activities = await harness.list_activities(chat_id, state.session_name)
    except JulesClientError as exc:
        await _send_error(update, f"Could not load activities: {exc}")
        return
    descriptor = harness.active_descriptor(chat_id)
    await _respond(
        update,
        _format_activities(activities, descriptor.display_name),
        markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Refresh", callback_data="session:activities")],
                [InlineKeyboardButton("Session status", callback_data="session:status")],
            ]
        ),
    )


async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List recent sessions and allow the user to open one."""

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    harness = _harness(context)
    try:
        sessions = await harness.list_sessions(chat_id)
    except JulesClientError as exc:
        await _send_error(update, f"Could not load sessions: {exc}")
        return
    state = harness.state_for(chat_id)
    state.session_options = {str(index): session.name for index, session in enumerate(sessions[:MAX_SESSION_BUTTONS])}
    if not sessions:
        await _respond(update, "<b>Sessions</b>\n\nNo sessions found.", markup=_home_markup())
        return
    lines = ["<b>Sessions</b>", "", "Choose a session to open it in this chat:"]
    buttons: list[list[InlineKeyboardButton]] = []
    for index, session in enumerate(sessions[:MAX_SESSION_BUTTONS]):
        title = session.title or session.identifier
        lines.append(f"{index + 1}. {_escape(title)} · {_escape(session.state_label)}")
        buttons.append([InlineKeyboardButton(f"{index + 1}. {title}"[:60], callback_data=f"session:open:{index}")])
    buttons.append([InlineKeyboardButton("Back to menu", callback_data="ui:home")])
    await _respond(update, "\n".join(lines), markup=InlineKeyboardMarkup(buttons))


async def new_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask for confirmation before clearing the local session link."""

    await _respond(
        update,
        "<b>Start a new task?</b>\n\nThis clears only the chat's local session link. It does not delete the remote session.",
        markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Confirm new task", callback_data="session:new:confirm"), InlineKeyboardButton("Cancel", callback_data="ui:home")]]
        ),
    )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route an ordinary text message through the active agent."""

    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.text:
        return
    prompt = message.text.strip()
    if not prompt:
        await message.reply_text("Please send a non-empty text message.")
        return
    harness = _harness(context)
    await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
    try:
        reply = await harness.ask(chat.id, prompt)
    except JulesClientError as exc:
        LOGGER.warning("Agent request failed for chat %s: %s", chat.id, exc)
        await message.reply_text("The active agent could not complete that request. Use Status or Activities to inspect the current task.")
        return
    except Exception:
        LOGGER.exception("Unexpected message handling failure for chat %s", chat.id)
        await message.reply_text("An unexpected error occurred. Please try again later.")
        return
    await _reply_in_chunks(message, reply.text, markup=_home_markup())


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch inline-keyboard actions to application use cases."""

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
        elif data == "agent:list":
            await agents_command(update, context)
        elif data.startswith("agent:pick:"):
            await _select_agent(update, context, data.rsplit(":", 1)[-1])
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
                _harness(context).store.reset_session(chat_id)
            await _show_home(update, context)
    except JulesClientError as exc:
        LOGGER.warning("Agent callback failed: %s", exc)
        await query.answer("The agent request failed. Please try again.", show_alert=True)
    except Exception:
        LOGGER.exception("Unexpected callback failure for %s", data)
        await query.answer("Something went wrong. Please try again.", show_alert=True)


async def _select_agent(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    """Switch the active agent for a chat and clear provider-specific session state."""

    query = update.callback_query
    chat_id = update.effective_chat.id if update.effective_chat else None
    if query is None or chat_id is None:
        return
    harness = _harness(context)
    state = harness.state_for(chat_id)
    agent_id = state.agent_options.get(key)
    if not agent_id:
        await query.answer("This agent list has expired. Open Agents again.", show_alert=True)
        return
    descriptor = harness.select_agent(chat_id, agent_id)
    await _show_home(update, context)
    await query.answer(f"Active agent: {descriptor.display_name}")


async def _select_source(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    """Store a selected repository and present its branches."""

    query = update.callback_query
    chat_id = update.effective_chat.id if update.effective_chat else None
    if query is None or chat_id is None:
        return
    state = _harness(context).state_for(chat_id)
    source = state.source_options.get(key)
    if source is None:
        await query.answer("This repository list has expired. Open Repositories again.", show_alert=True)
        return
    state.selected_source = source
    branches = list(source.branches) or ([source.default_branch] if source.default_branch else ["main"])
    state.branch_options = {str(index): branch for index, branch in enumerate(branches)}
    buttons = [[InlineKeyboardButton(branch, callback_data=f"source:branch:{index}")] for index, branch in state.branch_options.items()]
    buttons.append([InlineKeyboardButton("Back to repositories", callback_data="source:list")])
    await _edit_query(query, f"<b>{_escape(source.label)}</b>\n\nChoose the starting branch for the next session.", markup=InlineKeyboardMarkup(buttons))


async def _select_branch(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    """Store a selected branch and return to the main menu."""

    query = update.callback_query
    chat_id = update.effective_chat.id if update.effective_chat else None
    if query is None or chat_id is None:
        return
    state = _harness(context).state_for(chat_id)
    branch = state.branch_options.get(key)
    if branch is None or state.selected_source is None:
        await query.answer("This branch selection has expired. Open Repositories again.", show_alert=True)
        return
    state.selected_branch = branch
    await _edit_query(query, f"<b>Repository selected</b>\n\n<b>Repository:</b> {_escape(state.selected_source.label)}\n<b>Branch:</b> <code>{_escape(branch)}</code>\n\nYour next task will use this context.", markup=_home_markup())


async def _open_session(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    """Attach a selected remote session to the current chat."""

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    state = _harness(context).state_for(chat_id)
    session_name = state.session_options.get(key)
    if not session_name:
        await _send_error(update, "This session list has expired. Open All sessions again.")
        return
    state.session_name = session_name
    await session_command(update, context)


async def _confirm_approval(update: Update) -> None:
    """Ask for confirmation before approving a plan."""

    await _respond(update, "<b>Approve this plan?</b>\n\nThe active agent will continue executing the current session plan.", markup=InlineKeyboardMarkup([[InlineKeyboardButton("Approve plan", callback_data="session:approve:confirm"), InlineKeyboardButton("Cancel", callback_data="session:status")]]))


async def _approve_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Approve the active plan through the harness."""

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    state = _harness(context).state_for(chat_id)
    if not state.session_name:
        await _send_error(update, "There is no active session to approve.")
        return
    await _harness(context).approve_plan(chat_id, state.session_name)
    await session_command(update, context)


async def _confirm_end(update: Update) -> None:
    """Ask for confirmation before deleting a remote session."""

    await _respond(update, "<b>End and delete this session?</b>\n\nThis cannot be undone from the bot.", markup=InlineKeyboardMarkup([[InlineKeyboardButton("Delete session", callback_data="session:end:confirm"), InlineKeyboardButton("Cancel", callback_data="session:status")]]))


async def _end_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete the active session and clear the local chat association."""

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    harness = _harness(context)
    state = harness.state_for(chat_id)
    if state.session_name:
        await harness.delete_session(chat_id, state.session_name)
    harness.store.reset_session(chat_id)
    await _show_home(update, context)


async def _reply_in_chunks(message: Any, text: str, *, markup: InlineKeyboardMarkup | None = None) -> None:
    """Send agent text while respecting Telegram's message-size limit."""

    clean_text = text.strip() or "The agent returned an empty response."
    chunks = [clean_text[start : start + MAX_TELEGRAM_MESSAGE_LENGTH] for start in range(0, len(clean_text), MAX_TELEGRAM_MESSAGE_LENGTH)]
    for index, chunk in enumerate(chunks):
        await message.reply_text(chunk, reply_markup=markup if index == len(chunks) - 1 else None)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log unhandled update errors and notify the user when possible."""

    LOGGER.error("Unhandled Telegram update error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text("Something went wrong while processing your request.")
        except Exception:
            LOGGER.exception("Could not send a Telegram error message.")
