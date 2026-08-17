"""Asynchronous client for the Jules REST API."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import aiohttp


LOGGER = logging.getLogger(__name__)
JSONValue = dict[str, Any]


class JulesClientError(RuntimeError):
    """Base exception for Jules client failures."""


class JulesAPIError(JulesClientError):
    """Raised when Jules returns an unsuccessful HTTP response."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Jules API returned HTTP {status}: {message}")
        self.status = status
        self.message = message


@dataclass(frozen=True, slots=True)
class JulesSession:
    """Small representation of a Jules session used by the Telegram handler."""

    name: str


class JulesClient:
    """A reusable async Jules REST client.

    The official API is asynchronous: creating or messaging a session returns
    quickly, while the agent's response arrives later as a session activity.
    This client therefore sends the request and polls activities until it finds
    a new agent text or reaches the configured timeout.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        request_timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 2.0,
        reply_timeout_seconds: float = 120.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
        }
        self._timeout = aiohttp.ClientTimeout(total=request_timeout_seconds)
        self._poll_interval_seconds = poll_interval_seconds
        self._reply_timeout_seconds = reply_timeout_seconds
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> "JulesClient":
        """Open the underlying HTTP session when managed by this client."""

        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        """Close the underlying HTTP session when leaving the context."""

        await self.close()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Create the HTTP session lazily and return it."""

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers=self._headers,
            )
        return self._session

    async def close(self) -> None:
        """Close the owned HTTP session, if one exists."""

        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expected_statuses: tuple[int, ...] = (200,),
        payload: Mapping[str, Any] | None = None,
        params: Mapping[str, str | int] | None = None,
    ) -> JSONValue:
        """Make a Jules request and convert API errors into typed exceptions."""

        session = await self._ensure_session()
        url = f"{self._base_url}/{path.lstrip('/')}"
        try:
            async with session.request(method, url, json=payload, params=params) as response:
                raw_body = await response.text()
                if response.status not in expected_statuses:
                    message = raw_body[:500] or response.reason or "unknown error"
                    try:
                        error_payload = await self._decode_json(raw_body)
                    except JulesClientError:
                        error_payload = {}
                    message = str(
                        error_payload.get("error", {}).get("message", message)
                        if isinstance(error_payload.get("error"), Mapping)
                        else message
                    )
                    raise JulesAPIError(response.status, message)
                if not raw_body.strip():
                    return {}
                return await self._decode_json(raw_body)
        except asyncio.TimeoutError as exc:
            raise JulesClientError("The Jules API request timed out.") from exc
        except aiohttp.ClientError as exc:
            raise JulesClientError(f"Could not reach the Jules API: {exc}") from exc

    @staticmethod
    async def _decode_json(raw_body: str) -> JSONValue:
        """Decode a JSON object from an API response body."""

        import json

        try:
            decoded = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise JulesClientError("Jules returned an invalid JSON response.") from exc
        if not isinstance(decoded, dict):
            raise JulesClientError("Jules returned a JSON value other than an object.")
        return decoded

    async def create_session(self, prompt: str, title: str) -> JulesSession:
        """Create a Jules coding session for a Telegram request.

        A repository source is optional in this integration. If the API account
        has a configured default source, Jules can use the prompt directly. For
        repository-specific work, add ``JULES_SOURCE`` and ``JULES_STARTING_BRANCH``
        to the environment; those fields are then included in the request.
        """

        import os

        payload: JSONValue = {"prompt": prompt, "title": title}
        source = os.getenv("JULES_SOURCE", "").strip()
        if source:
            payload["sourceContext"] = {
                "source": source,
                "githubRepoContext": {
                    "startingBranch": os.getenv("JULES_STARTING_BRANCH", "main").strip()
                    or "main"
                },
            }
        automation_mode = os.getenv("JULES_AUTOMATION_MODE", "").strip()
        if automation_mode:
            payload["automationMode"] = automation_mode

        response = await self._request(
            "POST",
            "/sessions",
            expected_statuses=(200, 201),
            payload=payload,
        )
        session_name = self._session_name(response)
        if not session_name:
            raise JulesClientError("Jules created a session without returning its name.")
        return JulesSession(name=session_name)

    async def send_message(self, session_name: str, prompt: str) -> None:
        """Send a follow-up message to an existing Jules session."""

        await self._request(
            "POST",
            f"/{self._normalise_session_name(session_name)}:sendMessage",
            expected_statuses=(200, 202, 204),
            payload={"prompt": prompt},
        )

    async def list_activities(self, session_name: str) -> list[JSONValue]:
        """Return activities for a Jules session."""

        response = await self._request(
            "GET",
            f"/{self._normalise_session_name(session_name)}/activities",
            expected_statuses=(200,),
            params={"pageSize": 100},
        )
        activities = response.get("activities", [])
        if not isinstance(activities, list):
            raise JulesClientError("Jules returned an invalid activities payload.")
        return [item for item in activities if isinstance(item, dict)]

    async def ask(self, prompt: str, title: str) -> str:
        """Create a new session and wait for the first agent response."""

        session = await self.create_session(prompt, title)
        return await self.wait_for_agent_reply(session.name)

    async def continue_session(self, session_name: str, prompt: str) -> str:
        """Send a prompt to a session and wait for a new agent response."""

        known_activities = await self.list_activities(session_name)
        known_names = {
            activity_name
            for activity in known_activities
            if (activity_name := activity.get("name"))
        }
        await self.send_message(session_name, prompt)
        return await self.wait_for_agent_reply(session_name, known_names=known_names)

    async def wait_for_agent_reply(
        self,
        session_name: str,
        *,
        known_names: set[str] | None = None,
    ) -> str:
        """Poll activities until a new textual agent response is available."""

        known_names = known_names or set()
        deadline = time.monotonic() + self._reply_timeout_seconds
        while time.monotonic() < deadline:
            activities = await self.list_activities(session_name)
            for activity in activities:
                activity_name = activity.get("name")
                if activity_name in known_names:
                    continue
                reply = self._extract_agent_text(activity)
                if reply:
                    return reply
            await asyncio.sleep(self._poll_interval_seconds)

        raise JulesClientError(
            "Jules accepted the request but did not produce a text response "
            f"within {self._reply_timeout_seconds:.0f} seconds."
        )

    @staticmethod
    def _normalise_session_name(session_name: str) -> str:
        """Convert a session ID or resource name to a resource name."""

        return session_name if session_name.startswith("sessions/") else f"sessions/{session_name}"

    @classmethod
    def _session_name(cls, response: Mapping[str, Any]) -> str | None:
        """Extract and normalize a session resource name from a response."""

        nested_session = response.get("session")
        nested_name = nested_session.get("name") if isinstance(nested_session, Mapping) else None
        raw_name = response.get("name") or nested_name
        if not isinstance(raw_name, str) or not raw_name.strip():
            return None
        return cls._normalise_session_name(raw_name.strip())

    @staticmethod
    def _extract_agent_text(activity: Mapping[str, Any]) -> str | None:
        """Extract a human-readable response from a Jules activity object."""

        if activity.get("originator") not in (None, "agent"):
            return None
        agent_message = activity.get("agentMessaged")
        if isinstance(agent_message, Mapping):
            value = agent_message.get("agentMessage")
            if isinstance(value, str) and value.strip():
                return value.strip()
        for field in ("message", "text", "response"):
            value = activity.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, Mapping):
                for nested_field in ("text", "message", "prompt"):
                    nested_value = value.get(nested_field)
                    if isinstance(nested_value, str) and nested_value.strip():
                        return nested_value.strip()
        return None
