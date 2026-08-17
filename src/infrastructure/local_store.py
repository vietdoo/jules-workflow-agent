"""Filesystem-backed local state and append-only operational event storage."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.application.harness import ChatState, StateStore
from src.domain.agent import AgentSource, ConversationId


def _source_to_payload(source: AgentSource | None) -> dict[str, Any] | None:
    """Serialize an optional normalized source for the JSON state file."""

    if source is None:
        return None
    return {
        "name": source.name,
        "label": source.label,
        "default_branch": source.default_branch,
        "branches": list(source.branches),
        "metadata": source.metadata,
    }


def _source_from_payload(payload: dict[str, Any] | None) -> AgentSource | None:
    """Deserialize an optional normalized source from persisted JSON."""

    if not payload:
        return None
    return AgentSource(
        name=str(payload["name"]),
        label=str(payload.get("label") or payload["name"]),
        default_branch=payload.get("default_branch"),
        branches=tuple(payload.get("branches") or ()),
        metadata=dict(payload.get("metadata") or {}),
    )


def _state_to_payload(state: ChatState) -> dict[str, Any]:
    """Convert mutable harness state into JSON-compatible data."""

    return {
        "active_agent_id": state.active_agent_id,
        "session_name": state.session_name,
        "selected_source": _source_to_payload(state.selected_source),
        "selected_branch": state.selected_branch,
        "source_options": {key: _source_to_payload(value) for key, value in state.source_options.items()},
        "branch_options": state.branch_options,
        "session_options": state.session_options,
        "agent_options": state.agent_options,
    }


def _state_from_payload(payload: dict[str, Any], *, default_agent_id: str) -> ChatState:
    """Rehydrate mutable harness state from JSON-compatible data."""

    return ChatState(
        active_agent_id=str(payload.get("active_agent_id") or default_agent_id),
        session_name=payload.get("session_name"),
        selected_source=_source_from_payload(payload.get("selected_source")),
        selected_branch=payload.get("selected_branch"),
        source_options={
            key: source
            for key, value in dict(payload.get("source_options") or {}).items()
            if (source := _source_from_payload(value)) is not None
        },
        branch_options={str(key): str(value) for key, value in dict(payload.get("branch_options") or {}).items()},
        session_options={str(key): str(value) for key, value in dict(payload.get("session_options") or {}).items()},
        agent_options={str(key): str(value) for key, value in dict(payload.get("agent_options") or {}).items()},
    )


class JsonChatStateStore(StateStore):
    """Persist local chat state in one atomically written JSON document."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "state" / "conversations.json"
        self._states: dict[ConversationId, ChatState] = {}
        self._locks: defaultdict[ConversationId, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._load()

    def _load(self) -> None:
        """Load state once; ignore malformed local state rather than failing startup."""

        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            for key, value in dict(payload).items():
                self._states[key] = _state_from_payload(dict(value), default_agent_id="")
        except (OSError, json.JSONDecodeError, TypeError, KeyError):
            return

    def _persist(self) -> None:
        """Atomically replace the JSON state file after a local state mutation."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {str(key): _state_to_payload(value) for key, value in self._states.items()}
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False, dir=self.path.parent, prefix=".conversations-"
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            temporary_path = Path(handle.name)
        os.replace(temporary_path, self.path)

    def state_for(self, conversation_id: ConversationId, *, default_agent_id: str = "") -> ChatState:
        """Return state for a conversation and save first-use initialization."""

        key = str(conversation_id)
        state = self._states.get(key)
        if state is None:
            state = ChatState(active_agent_id=default_agent_id)
            self._states[key] = state
            self._persist()
        elif not state.active_agent_id and default_agent_id:
            state.active_agent_id = default_agent_id
            self._persist()
        return state

    def lock_for(self, conversation_id: ConversationId) -> asyncio.Lock:
        """Return a process-local lock that serializes provider calls per conversation."""

        return self._locks[str(conversation_id)]

    def reset_session(self, conversation_id: ConversationId) -> None:
        """Clear provider session state and persist the change."""

        state = self.state_for(conversation_id)
        state.session_name = None
        state.source_options.clear()
        state.branch_options.clear()
        state.session_options.clear()
        self._persist()

    def save(self) -> None:
        """Persist in-place state changes made by application services."""

        self._persist()


class LocalEventStore:
    """Append full local audit events to JSONL and readable Markdown journals."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = asyncio.Lock()

    def _paths(self, timestamp: datetime) -> tuple[Path, Path]:
        date_key = timestamp.date().isoformat()
        return (
            self.root / "events" / f"{date_key}.jsonl",
            self.root / "journals" / f"{date_key}.md",
        )

    async def record(
        self,
        event_type: str,
        *,
        conversation_id: ConversationId,
        agent_id: str | None = None,
        session_name: str | None = None,
        summary: str = "",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one structured and human-readable local audit event."""

        timestamp = datetime.now(UTC)
        event = {
            "id": uuid4().hex,
            "timestamp": timestamp.isoformat(),
            "type": event_type,
            "conversation_id": str(conversation_id),
            "agent_id": agent_id,
            "session_name": session_name,
            "summary": summary,
            "data": data or {},
        }
        async with self._lock:
            await asyncio.to_thread(self._append_sync, event, timestamp)
        return event

    def _append_sync(self, event: dict[str, Any], timestamp: datetime) -> None:
        jsonl_path, markdown_path = self._paths(timestamp)
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        with markdown_path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"\n## {event['timestamp']} — `{event['type']}`\n\n"
                f"- **Conversation:** `{event['conversation_id']}`\n"
                f"- **Agent:** `{event['agent_id'] or 'n/a'}`\n"
                f"- **Session:** `{event['session_name'] or 'n/a'}`\n"
                f"- **Summary:** {event['summary'] or 'No summary'}\n\n"
                "```json\n"
                f"{json.dumps(event['data'], ensure_ascii=False, indent=2, sort_keys=True)}\n"
                "```\n"
            )

    async def recent(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Read recent events across the local append-only journals."""

        return await asyncio.to_thread(self._recent_sync, limit)

    def _recent_sync(self, limit: int) -> list[dict[str, Any]]:
        events_dir = self.root / "events"
        if not events_dir.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(events_dir.glob("*.jsonl"), reverse=True):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if len(records) >= limit:
                    return records
        return records

    async def summary(self) -> dict[str, int]:
        """Return lightweight counts for the local dashboard."""

        events = await self.recent(limit=10_000)
        return {
            "events": len(events),
            "messages": sum(event.get("type") == "message.completed" for event in events),
            "failures": sum(event.get("type", "").endswith("failed") for event in events),
            "sessions": len({event.get("session_name") for event in events if event.get("session_name")}),
        }
