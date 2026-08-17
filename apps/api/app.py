"""FastAPI transport adapter for the local multi-interface agent harness."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.api.jules_client import JulesAPIError
from src.application.control_plane import LocalControlPlane
from src.bootstrap import build_agent_harness
from src.config import Settings, get_settings
from src.infrastructure.local_store import JsonChatStateStore, LocalEventStore

LOGGER = logging.getLogger(__name__)
DEFAULT_CONVERSATION_ID = "web:local"


class ConversationBody(BaseModel):
    """Identify a browser workspace without coupling the API to a user database."""

    conversation_id: str = Field(default=DEFAULT_CONVERSATION_ID, min_length=1, max_length=160)


class AgentSelectionBody(ConversationBody):
    """Request body used to activate a registered agent."""

    agent_id: str = Field(min_length=1, max_length=80)


class SourceSelectionBody(ConversationBody):
    """Request body used to select a source and optional starting branch."""

    source_name: str = Field(min_length=1, max_length=500)
    branch: str | None = Field(default=None, max_length=250)


class PromptBody(ConversationBody):
    """Request body used to start or continue provider work."""

    prompt: str = Field(min_length=1, max_length=20_000)


class SessionActionBody(ConversationBody):
    """Request body used for destructive or approval session actions."""

    confirm: bool = False


ConversationQuery = Annotated[str, Query(min_length=1, max_length=160)]


def _root_path(settings: Settings) -> Path:
    """Resolve local persistence relative to the repository working directory."""

    return Path(settings.local_data_dir).expanduser().resolve()


def _raise_http_error(exc: Exception) -> None:
    """Map provider and validation failures to safe browser-facing API errors."""

    if isinstance(exc, JulesAPIError):
        raise HTTPException(status_code=exc.status or 502, detail=exc.message) from exc
    if isinstance(exc, (KeyError, LookupError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    LOGGER.exception("control_plane_unexpected_error")
    raise HTTPException(status_code=502, detail="The active agent could not complete this request.") from exc


def create_app(
    settings: Settings | None = None,
    *,
    control_plane: LocalControlPlane | None = None,
) -> FastAPI:
    """Build the local web API with injectable dependencies for tests."""

    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if control_plane is None:
            root = _root_path(resolved_settings)
            store = JsonChatStateStore(root)
            harness = build_agent_harness(resolved_settings, store=store)
            app.state.control_plane = LocalControlPlane(harness, store, LocalEventStore(root))
        else:
            app.state.control_plane = control_plane
        LOGGER.info("local_control_plane_started host=%s port=%s", resolved_settings.web_api_host, resolved_settings.web_api_port)
        try:
            yield
        finally:
            if control_plane is None:
                await app.state.control_plane.harness.close()
            LOGGER.info("local_control_plane_stopped")

    app = FastAPI(
        title="Jules Workflow Local Control Plane",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.web_cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    def plane() -> LocalControlPlane:
        return app.state.control_plane

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        """Expose local service health without making an external provider call."""

        return {
            "status": "ok",
            "mode": "local",
            "agents": [item.agent_id for item in plane().harness.registry.descriptors()],
        }

    @app.get("/api/dashboard")
    async def dashboard(conversation_id: ConversationQuery = DEFAULT_CONVERSATION_ID) -> dict[str, Any]:
        """Return local state, event counts, registered agents, and recent activity."""

        try:
            return await plane().dashboard(conversation_id)
        except Exception as exc:
            _raise_http_error(exc)

    @app.get("/api/agents")
    async def agents(conversation_id: ConversationQuery = DEFAULT_CONVERSATION_ID) -> dict[str, Any]:
        """List registered agents and the active agent for this workspace."""

        return await plane().agents(conversation_id)

    @app.put("/api/agents/active")
    async def select_agent(payload: AgentSelectionBody) -> dict[str, Any]:
        """Activate an agent for a web workspace and clear its old session context."""

        try:
            return await plane().select_agent(payload.conversation_id, payload.agent_id)
        except Exception as exc:
            _raise_http_error(exc)

    @app.get("/api/sources")
    async def sources(conversation_id: ConversationQuery = DEFAULT_CONVERSATION_ID) -> list[dict[str, Any]]:
        """List sources available from the active provider adapter."""

        try:
            return await plane().sources(conversation_id)
        except Exception as exc:
            _raise_http_error(exc)

    @app.put("/api/sources/active")
    async def select_source(payload: SourceSelectionBody) -> dict[str, Any]:
        """Select a source and branch for subsequent prompts."""

        try:
            return await plane().select_source(payload.conversation_id, payload.source_name, payload.branch)
        except Exception as exc:
            _raise_http_error(exc)

    @app.get("/api/sessions")
    async def sessions(conversation_id: ConversationQuery = DEFAULT_CONVERSATION_ID) -> list[dict[str, Any]]:
        """List remote sessions exposed by the active provider."""

        try:
            return await plane().sessions(conversation_id)
        except Exception as exc:
            _raise_http_error(exc)

    @app.get("/api/sessions/{session_name:path}/activities")
    async def activities(
        session_name: str, conversation_id: ConversationQuery = DEFAULT_CONVERSATION_ID
    ) -> list[dict[str, Any]]:
        """Return a provider activity timeline for one session."""

        try:
            return await plane().activities(conversation_id, session_name)
        except Exception as exc:
            _raise_http_error(exc)

    @app.get("/api/sessions/{session_name:path}")
    async def session(session_name: str, conversation_id: ConversationQuery = DEFAULT_CONVERSATION_ID) -> dict[str, Any]:
        """Return normalized metadata for a single provider session."""

        try:
            return await plane().session(conversation_id, session_name)
        except Exception as exc:
            _raise_http_error(exc)

    @app.post("/api/messages")
    async def send_message(payload: PromptBody) -> dict[str, Any]:
        """Queue agent work and return before the asynchronous provider reply arrives."""

        try:
            return await plane().submit_message(payload.conversation_id, payload.prompt)
        except Exception as exc:
            _raise_http_error(exc)

    @app.post("/api/sessions/{session_name:path}/attach")
    async def attach_session(session_name: str, payload: ConversationBody) -> dict[str, Any]:
        """Attach an existing provider session to the active Studio workspace."""

        try:
            return await plane().attach_session(payload.conversation_id, session_name)
        except Exception as exc:
            _raise_http_error(exc)

    @app.post("/api/sessions/{session_name:path}/approve")
    async def approve_plan(session_name: str, payload: SessionActionBody) -> dict[str, bool]:
        """Approve a provider plan only after the browser sends explicit confirmation."""

        if not payload.confirm:
            raise HTTPException(status_code=400, detail="Explicit confirmation is required to approve a plan.")
        try:
            await plane().approve_plan(payload.conversation_id, session_name)
            return {"ok": True}
        except Exception as exc:
            _raise_http_error(exc)

    @app.post("/api/sessions/reset")
    async def reset_session(payload: ConversationBody) -> dict[str, Any]:
        """Clear the local active-session association without deleting remote work."""

        return await plane().reset_session(payload.conversation_id)

    @app.delete("/api/sessions/{session_name:path}")
    async def delete_session(session_name: str, payload: SessionActionBody) -> dict[str, bool]:
        """Delete one remote session only after explicit browser confirmation."""

        if not payload.confirm:
            raise HTTPException(status_code=400, detail="Explicit confirmation is required to delete a session.")
        try:
            await plane().delete_session(payload.conversation_id, session_name)
            return {"ok": True}
        except Exception as exc:
            _raise_http_error(exc)

    @app.get("/api/events")
    async def events(
        conversation_id: ConversationQuery = DEFAULT_CONVERSATION_ID,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        """Return recent JSONL-backed audit events for one browser workspace."""

        return await plane().recent_events(conversation_id, limit=limit)

    @app.websocket("/api/events/stream")
    async def event_stream(websocket: WebSocket, conversation_id: str = DEFAULT_CONVERSATION_ID) -> None:
        """Poll local append-only events and publish deltas to a browser workspace."""

        await websocket.accept()
        last_event_id = ""
        try:
            while True:
                recent = await plane().recent_events(conversation_id, limit=40)
                ordered = list(reversed(recent))
                if last_event_id:
                    new_events = [event for event in ordered if event["id"] > last_event_id]
                else:
                    new_events = ordered[-12:]
                for event in new_events:
                    await websocket.send_json(event)
                    last_event_id = event["id"]
                await asyncio.sleep(1.2)
        except WebSocketDisconnect:
            return

    return app
