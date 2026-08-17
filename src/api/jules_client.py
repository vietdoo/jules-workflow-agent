"""Asynchronous client for the official Jules v1alpha REST API."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import aiohttp


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
class JulesSource:
    """Connected GitHub repository exposed by the Jules Sources API."""

    name: str
    identifier: str
    owner: str | None = None
    repository: str | None = None
    is_private: bool | None = None
    default_branch: str | None = None
    branches: tuple[str, ...] = ()
    raw: JSONValue = field(default_factory=dict, repr=False)

    @property
    def label(self) -> str:
        """Return a compact human-readable repository label."""

        if self.owner and self.repository:
            return f"{self.owner}/{self.repository}"
        return self.identifier or self.name


@dataclass(frozen=True, slots=True)
class JulesSession:
    """Normalized Jules session metadata used by the Telegram UI."""

    name: str
    identifier: str
    title: str | None = None
    state: str | None = None
    url: str | None = None
    source_name: str | None = None
    starting_branch: str | None = None
    prompt: str | None = None
    require_plan_approval: bool | None = None
    raw: JSONValue = field(default_factory=dict, repr=False)

    @property
    def state_label(self) -> str:
        """Return a user-friendly session state label."""

        return (self.state or "UNKNOWN").replace("_", " ").title()


class JulesClient:
    """Reusable asynchronous client for Jules REST resources.

    Jules creates sessions quickly and reports work asynchronously through
    activities. This client handles the complete documented resource surface
    needed by the Telegram bot and provides a polling helper for chat replies.
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
                        error_payload = json.loads(raw_body)
                    except json.JSONDecodeError:
                        error_payload = {}
                    if isinstance(error_payload, Mapping):
                        error_details = error_payload.get("error")
                        if isinstance(error_details, Mapping):
                            message = str(error_details.get("message", message))
                    raise JulesAPIError(response.status, message)
                if not raw_body.strip():
                    return {}
                try:
                    decoded = json.loads(raw_body)
                except json.JSONDecodeError as exc:
                    raise JulesClientError("Jules returned an invalid JSON response.") from exc
                if not isinstance(decoded, dict):
                    raise JulesClientError("Jules returned a JSON value other than an object.")
                return decoded
        except asyncio.TimeoutError as exc:
            raise JulesClientError("The Jules API request timed out.") from exc
        except aiohttp.ClientError as exc:
            raise JulesClientError(f"Could not reach the Jules API: {exc}") from exc

    async def list_sources(self, *, page_size: int = 100) -> list[JulesSource]:
        """List every connected GitHub source, following pagination tokens."""

        sources: list[JulesSource] = []
        page_token: str | None = None
        while True:
            params: dict[str, str | int] = {"pageSize": min(max(page_size, 1), 100)}
            if page_token:
                params["pageToken"] = page_token
            response = await self._request("GET", "/sources", params=params)
            raw_sources = response.get("sources", [])
            if not isinstance(raw_sources, list):
                raise JulesClientError("Jules returned an invalid sources payload.")
            sources.extend(
                self._parse_source(item) for item in raw_sources if isinstance(item, dict)
            )
            page_token = response.get("nextPageToken")
            if not isinstance(page_token, str) or not page_token:
                return sources

    async def get_source(self, source_name: str) -> JulesSource:
        """Retrieve one connected source by resource name."""

        response = await self._request("GET", f"/{self._normalise_source_name(source_name)}")
        return self._parse_source(response)

    async def create_session(
        self,
        prompt: str,
        title: str,
        *,
        source_name: str | None = None,
        starting_branch: str | None = None,
        require_plan_approval: bool = False,
        automation_mode: str | None = None,
    ) -> JulesSession:
        """Create a Jules session with optional repository and automation context."""

        payload: JSONValue = {
            "prompt": prompt,
            "title": title,
            "requirePlanApproval": require_plan_approval,
        }
        if source_name:
            payload["sourceContext"] = {
                "source": self._normalise_source_name(source_name),
                "githubRepoContext": {"startingBranch": starting_branch or "main"},
            }
        if automation_mode:
            payload["automationMode"] = automation_mode

        response = await self._request(
            "POST",
            "/sessions",
            expected_statuses=(200, 201),
            payload=payload,
        )
        session = self._parse_session(response)
        if not session.name:
            raise JulesClientError("Jules created a session without returning its name.")
        return session

    async def list_sessions(self, *, page_size: int = 100) -> list[JulesSession]:
        """List every Jules session, following pagination tokens."""

        sessions: list[JulesSession] = []
        page_token: str | None = None
        while True:
            params: dict[str, str | int] = {"pageSize": min(max(page_size, 1), 100)}
            if page_token:
                params["pageToken"] = page_token
            response = await self._request("GET", "/sessions", params=params)
            raw_sessions = response.get("sessions", [])
            if not isinstance(raw_sessions, list):
                raise JulesClientError("Jules returned an invalid sessions payload.")
            sessions.extend(
                self._parse_session(item) for item in raw_sessions if isinstance(item, dict)
            )
            page_token = response.get("nextPageToken")
            if not isinstance(page_token, str) or not page_token:
                return sessions

    async def get_session(self, session_name: str) -> JulesSession:
        """Retrieve the latest metadata for a Jules session."""

        response = await self._request(
            "GET", f"/{self._normalise_session_name(session_name)}"
        )
        return self._parse_session(response)

    async def delete_session(self, session_name: str) -> None:
        """Delete a Jules session using the documented DELETE endpoint."""

        await self._request(
            "DELETE",
            f"/{self._normalise_session_name(session_name)}",
            expected_statuses=(200, 202, 204),
        )

    async def send_message(self, session_name: str, prompt: str) -> None:
        """Send a follow-up prompt to an active Jules session."""

        await self._request(
            "POST",
            f"/{self._normalise_session_name(session_name)}:sendMessage",
            expected_statuses=(200, 202, 204),
            payload={"prompt": prompt},
        )

    async def approve_plan(self, session_name: str) -> None:
        """Approve the pending plan for a session."""

        await self._request(
            "POST",
            f"/{self._normalise_session_name(session_name)}:approvePlan",
            expected_statuses=(200, 202, 204),
            payload={},
        )

    async def list_activities(
        self,
        session_name: str,
        *,
        page_size: int = 100,
    ) -> list[JSONValue]:
        """List all activities for a session, following pagination tokens."""

        activities: list[JSONValue] = []
        page_token: str | None = None
        while True:
            params: dict[str, str | int] = {"pageSize": min(max(page_size, 1), 100)}
            if page_token:
                params["pageToken"] = page_token
            response = await self._request(
                "GET",
                f"/{self._normalise_session_name(session_name)}/activities",
                params=params,
            )
            raw_activities = response.get("activities", [])
            if not isinstance(raw_activities, list):
                raise JulesClientError("Jules returned an invalid activities payload.")
            activities.extend(item for item in raw_activities if isinstance(item, dict))
            page_token = response.get("nextPageToken")
            if not isinstance(page_token, str) or not page_token:
                return activities

    async def get_activity(self, activity_name: str) -> JSONValue:
        """Retrieve one activity by full resource name."""

        return await self._request("GET", f"/{activity_name.lstrip('/')}")

    async def ask(self, prompt: str, title: str, **kwargs: Any) -> str:
        """Create a session and wait for its first agent message."""

        session = await self.create_session(prompt, title, **kwargs)
        return await self.wait_for_agent_reply(session.name)

    async def continue_session(self, session_name: str, prompt: str) -> str:
        """Send a prompt to a session and wait for a new agent message."""

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
        """Poll activities until a new agent message, failure, or timeout occurs."""

        known_names = known_names or set()
        deadline = time.monotonic() + self._reply_timeout_seconds
        while time.monotonic() < deadline:
            try:
                activities = await self.list_activities(session_name)
            except JulesAPIError as exc:
                # Jules may return the new Session before its Activity collection is
                # queryable. Treat a temporary 404 during this bounded poll as an
                # eventual-consistency delay, not as a failed user task.
                if exc.status != 404:
                    raise
                await asyncio.sleep(self._poll_interval_seconds)
                continue
            for activity in activities:
                activity_name = activity.get("name")
                if activity_name in known_names:
                    continue
                if (failure := self._extract_failure(activity)):
                    raise JulesClientError(f"Jules session failed: {failure}")
                if (reply := self._extract_agent_text(activity)):
                    return reply
            await asyncio.sleep(self._poll_interval_seconds)

        raise JulesClientError(
            "Jules accepted the request but did not produce a text response "
            f"within {self._reply_timeout_seconds:.0f} seconds."
        )

    @staticmethod
    def _parse_source(response: Mapping[str, Any]) -> JulesSource:
        """Normalize a source response into a typed repository object."""

        github_repo = response.get("githubRepo")
        github_repo = github_repo if isinstance(github_repo, Mapping) else {}
        default_branch = github_repo.get("defaultBranch")
        default_branch = (
            default_branch.get("displayName")
            if isinstance(default_branch, Mapping)
            else default_branch
        )
        raw_branches = github_repo.get("branches", [])
        branches = tuple(
            branch.get("displayName")
            for branch in raw_branches
            if isinstance(branch, Mapping) and isinstance(branch.get("displayName"), str)
        ) if isinstance(raw_branches, list) else ()
        name = str(response.get("name", ""))
        identifier = str(response.get("id", name.rsplit("/", 1)[-1]))
        return JulesSource(
            name=name,
            identifier=identifier,
            owner=github_repo.get("owner") if isinstance(github_repo.get("owner"), str) else None,
            repository=github_repo.get("repo") if isinstance(github_repo.get("repo"), str) else None,
            is_private=github_repo.get("isPrivate") if isinstance(github_repo.get("isPrivate"), bool) else None,
            default_branch=default_branch if isinstance(default_branch, str) else None,
            branches=branches,
            raw=dict(response),
        )

    @classmethod
    def _parse_session(cls, response: Mapping[str, Any]) -> JulesSession:
        """Normalize a session response into a typed session object."""

        raw_name = response.get("name")
        if not isinstance(raw_name, str):
            raw_name = ""
        name = cls._normalise_session_name(raw_name) if raw_name else ""
        identifier = response.get("id")
        if not isinstance(identifier, str) or not identifier:
            identifier = name.rsplit("/", 1)[-1] if name else ""
        source_name: str | None = None
        starting_branch: str | None = None
        source_context = response.get("sourceContext")
        if isinstance(source_context, Mapping):
            raw_source = source_context.get("source")
            source_name = raw_source if isinstance(raw_source, str) else None
            github_context = source_context.get("githubRepoContext")
            if isinstance(github_context, Mapping):
                raw_branch = github_context.get("startingBranch")
                starting_branch = raw_branch if isinstance(raw_branch, str) else None
        return JulesSession(
            name=name,
            identifier=identifier,
            title=response.get("title") if isinstance(response.get("title"), str) else None,
            state=response.get("state") if isinstance(response.get("state"), str) else None,
            url=response.get("url") if isinstance(response.get("url"), str) else None,
            source_name=source_name,
            starting_branch=starting_branch,
            prompt=response.get("prompt") if isinstance(response.get("prompt"), str) else None,
            require_plan_approval=(
                response.get("requirePlanApproval")
                if isinstance(response.get("requirePlanApproval"), bool)
                else None
            ),
            raw=dict(response),
        )

    @staticmethod
    def _normalise_session_name(session_name: str) -> str:
        """Convert a session ID or resource name to a resource name."""

        return session_name if session_name.startswith("sessions/") else f"sessions/{session_name}"

    @staticmethod
    def _normalise_source_name(source_name: str) -> str:
        """Convert a source ID or resource name to a resource name."""

        return source_name if source_name.startswith("sources/") else f"sources/{source_name}"

    @staticmethod
    def _extract_agent_text(activity: Mapping[str, Any]) -> str | None:
        """Extract the documented agent message from an activity."""

        if activity.get("originator") not in (None, "agent"):
            return None
        agent_message = activity.get("agentMessaged")
        if isinstance(agent_message, Mapping):
            value = agent_message.get("agentMessage")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _extract_failure(activity: Mapping[str, Any]) -> str | None:
        """Extract a documented session failure reason, if present."""

        failed = activity.get("sessionFailed")
        if isinstance(failed, Mapping):
            reason = failed.get("reason")
            if isinstance(reason, str) and reason.strip():
                return reason.strip()
        return None
