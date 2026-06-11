"""LiteLLM Agent Platform HTTP client for AgentRunner.

This module intentionally depends only on httpx and plain Python data shapes.
It supports two compatible targets:

- LiteLLM Agent Platform API: /session...
- lite-harness Managed Agents V0: /v1/sessions...
"""

from __future__ import annotations

import asyncio
import time
import typing

import httpx


class LiteLLMAgentPlatformAPIError(Exception):
    """LiteLLM Agent Platform API error."""

    def __init__(
        self,
        message: str,
        code: str = "litellm_agent_platform.api_error",
        *,
        retryable: bool = False,
    ):
        self.message = message
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class LiteLLMAgentPlatformConfigError(Exception):
    """LiteLLM Agent Platform configuration error."""

    def __init__(self, message: str, code: str = "litellm_agent_platform.config_invalid"):
        self.message = message
        self.code = code
        super().__init__(message)


def extract_text_from_parts(parts: typing.Any) -> str:
    """Extract assistant text from a platform parts array."""
    if not isinstance(parts, list):
        return ""

    chunks: list[str] = []
    for part in parts:
        if isinstance(part, str):
            chunks.append(part)
            continue
        if not isinstance(part, dict):
            continue

        if isinstance(part.get("text"), str):
            chunks.append(part["text"])
        elif isinstance(part.get("content"), str):
            chunks.append(part["content"])
        elif isinstance(part.get("artifact"), dict):
            artifact = part["artifact"]
            name = artifact.get("name") or artifact.get("id") or "artifact"
            url = artifact.get("url")
            chunks.append(f"[{name}]({url})" if url else str(name))

    return "".join(chunks)


def extract_text_from_response(value: typing.Any) -> str:
    """Extract readable assistant text from supported response shapes."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(extract_text_from_response(item) for item in value)
    if not isinstance(value, dict):
        return str(value)

    parts_text = extract_text_from_parts(value.get("parts"))
    if parts_text:
        return parts_text

    for key in ("content", "text", "result", "message", "answer", "output"):
        item = value.get(key)
        if isinstance(item, str) and item:
            return item

    response = value.get("response")
    if isinstance(response, (dict, list, str)):
        text = extract_text_from_response(response)
        if text:
            return text

    data = value.get("data")
    if isinstance(data, (dict, list, str)):
        text = extract_text_from_response(data)
        if text:
            return text

    return ""


def content_blocks_to_text(content: typing.Any) -> str:
    """Extract text from managed-agents event content blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return extract_text_from_parts(content)


def extract_assistant_text_from_messages(messages: typing.Any) -> str:
    """Extract assistant text from LiteLLM Agent Platform message rows."""
    if not isinstance(messages, list):
        return ""

    chunks: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        info = message.get("info")
        role = info.get("role") if isinstance(info, dict) else None
        if role != "assistant":
            continue
        parts_text = extract_text_from_parts(message.get("parts"))
        if parts_text:
            chunks.append(parts_text)

    return "".join(chunks)


class AsyncLiteLLMAgentPlatformClient:
    """Async HTTP client for LiteLLM Agent Platform compatible APIs."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: float = 300.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, typing.Any] | None = None,
    ) -> typing.Any:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=True) as client:
            try:
                response = await client.request(method, url, headers=self._headers(), json=json)
            except httpx.TimeoutException:
                raise LiteLLMAgentPlatformAPIError(
                    f"LiteLLM Agent Platform request timed out after {self.timeout}s",
                    code="litellm_agent_platform.timeout",
                    retryable=True,
                ) from None
            except httpx.TransportError as e:
                raise LiteLLMAgentPlatformAPIError(
                    f"LiteLLM Agent Platform request failed: {e}",
                    code="litellm_agent_platform.transport_error",
                    retryable=True,
                ) from None

        body_text = response.text
        if response.status_code >= 400:
            message = body_text[:500] or response.reason_phrase
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    error = payload.get("error")
                    if isinstance(error, dict):
                        message = str(error.get("message") or error)
                    elif error:
                        message = str(error)
            except ValueError:
                pass
            raise LiteLLMAgentPlatformAPIError(
                f"LiteLLM Agent Platform HTTP {response.status_code}: {message}",
                code="litellm_agent_platform.http_error",
                retryable=response.status_code >= 500,
            )

        if not body_text.strip():
            return {}
        try:
            return response.json()
        except ValueError:
            raise LiteLLMAgentPlatformAPIError(
                f"LiteLLM Agent Platform returned invalid JSON: {body_text[:200]}",
                code="litellm_agent_platform.response_invalid",
            ) from None

    async def create_platform_session(
        self,
        agent_id: str,
        *,
        title: str = "",
    ) -> dict[str, typing.Any]:
        payload: dict[str, typing.Any] = {"agent": agent_id}
        if title:
            payload["title"] = title
        data = await self._request_json(
            "POST",
            "/session",
            json=payload,
        )
        if not isinstance(data, dict):
            raise LiteLLMAgentPlatformAPIError(
                f"Unexpected create session response: {data!r}",
                code="litellm_agent_platform.response_invalid",
            )
        return data

    async def get_platform_session(self, session_id: str) -> dict[str, typing.Any]:
        data = await self._request_json(
            "GET",
            f"/session/{session_id}",
        )
        if not isinstance(data, dict):
            raise LiteLLMAgentPlatformAPIError(
                f"Unexpected session response: {data!r}",
                code="litellm_agent_platform.response_invalid",
            )
        return data

    async def wait_platform_session_ready(
        self,
        session_id: str,
        *,
        timeout: float,
        poll_interval: float,
    ) -> dict[str, typing.Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, typing.Any] | None = None
        while time.monotonic() < deadline:
            last = await self.get_platform_session(session_id)
            status = str(last.get("status") or "")
            if status not in {"failed", "dead", "stopped", "error"}:
                return last
            reason = last.get("failure_reason") or f"session status is {status}"
            raise LiteLLMAgentPlatformAPIError(
                f"LiteLLM Agent Platform session {session_id} is {status}: {reason}",
                code="litellm_agent_platform.session_not_ready",
            )
            await asyncio.sleep(max(0.1, poll_interval))

        detail = f"last status: {last.get('status')}" if last else "session was not readable"
        raise LiteLLMAgentPlatformAPIError(
            f"LiteLLM Agent Platform session {session_id} was not ready after {timeout}s ({detail})",
            code="litellm_agent_platform.session_ready_timeout",
            retryable=True,
        )

    async def send_platform_message(self, session_id: str, text: str) -> dict[str, typing.Any]:
        data = await self._request_json(
            "POST",
            f"/session/{session_id}/message",
            json={
                "model": {"modelID": ""},
                "parts": [{"type": "text", "text": text}],
            },
        )
        if not isinstance(data, (dict, list)):
            raise LiteLLMAgentPlatformAPIError(
                f"Unexpected message response: {data!r}",
                code="litellm_agent_platform.response_invalid",
            )
        return data

    async def list_platform_messages(self, session_id: str) -> list[dict[str, typing.Any]]:
        data = await self._request_json("GET", f"/session/{session_id}/message")
        if not isinstance(data, list):
            raise LiteLLMAgentPlatformAPIError(
                f"Unexpected message list response: {data!r}",
                code="litellm_agent_platform.response_invalid",
            )
        return [item for item in data if isinstance(item, dict)]

    async def send_platform_message_and_wait(
        self,
        session_id: str,
        text: str,
        *,
        model: str = "",
        timeout: float,
        poll_interval: float,
    ) -> tuple[str, list[dict[str, typing.Any]]]:
        before = await self.list_platform_messages(session_id)
        baseline = len(before)
        await self._request_json(
            "POST",
            f"/session/{session_id}/message",
            json={
                "model": {"modelID": model},
                "parts": [{"type": "text", "text": text}],
            },
        )

        deadline = time.monotonic() + timeout
        latest_messages: list[dict[str, typing.Any]] = []
        while time.monotonic() < deadline:
            messages = await self.list_platform_messages(session_id)
            latest_messages = messages[baseline:]
            content = extract_assistant_text_from_messages(latest_messages)
            if content:
                return content, latest_messages
            await asyncio.sleep(max(0.1, poll_interval))

        raise LiteLLMAgentPlatformAPIError(
            f"LiteLLM Agent Platform session {session_id} did not produce an assistant message after {timeout}s",
            code="litellm_agent_platform.message_timeout",
            retryable=True,
        )

    async def create_managed_session(self, harness: str, model: str = "") -> dict[str, typing.Any]:
        payload: dict[str, typing.Any] = {"agent": harness}
        if model:
            payload["model"] = model
        data = await self._request_json("POST", "/v1/sessions", json=payload)
        if not isinstance(data, dict):
            raise LiteLLMAgentPlatformAPIError(
                f"Unexpected managed session response: {data!r}",
                code="litellm_agent_platform.response_invalid",
            )
        return data

    async def get_managed_session(self, session_id: str) -> dict[str, typing.Any]:
        data = await self._request_json("GET", f"/v1/sessions/{session_id}")
        if not isinstance(data, dict):
            raise LiteLLMAgentPlatformAPIError(
                f"Unexpected managed session response: {data!r}",
                code="litellm_agent_platform.response_invalid",
            )
        return data

    async def list_managed_events(self, session_id: str) -> list[dict[str, typing.Any]]:
        data = await self._request_json("GET", f"/v1/sessions/{session_id}/events")
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise LiteLLMAgentPlatformAPIError(
                f"Unexpected managed events response: {data!r}",
                code="litellm_agent_platform.response_invalid",
            )
        return [item for item in data["data"] if isinstance(item, dict)]

    async def send_managed_event(self, session_id: str, text: str) -> None:
        await self._request_json(
            "POST",
            f"/v1/sessions/{session_id}/events",
            json={"events": [{"type": "user.message", "content": [{"type": "text", "text": text}]}]},
        )

    async def send_managed_message_and_wait(
        self,
        session_id: str,
        text: str,
        *,
        timeout: float,
        poll_interval: float,
    ) -> tuple[str, list[dict[str, typing.Any]]]:
        before = await self.list_managed_events(session_id)
        baseline = len(before)
        await self.send_managed_event(session_id, text)

        deadline = time.monotonic() + timeout
        latest_events: list[dict[str, typing.Any]] = []
        while time.monotonic() < deadline:
            events = await self.list_managed_events(session_id)
            latest_events = events[baseline:]
            error = next((ev for ev in latest_events if ev.get("type") == "session.status_error"), None)
            if error:
                raise LiteLLMAgentPlatformAPIError(
                    f"lite-harness managed session error: {error.get('error') or error}",
                    code="litellm_agent_platform.managed_session_error",
                )

            if any(ev.get("type") == "session.status_idle" for ev in latest_events):
                text_chunks = [
                    content_blocks_to_text(ev.get("content"))
                    for ev in latest_events
                    if ev.get("type") == "agent.message"
                ]
                return "".join(chunk for chunk in text_chunks if chunk), latest_events

            await asyncio.sleep(max(0.1, poll_interval))

        raise LiteLLMAgentPlatformAPIError(
            f"lite-harness managed session {session_id} did not finish after {timeout}s",
            code="litellm_agent_platform.managed_session_timeout",
            retryable=True,
        )


def session_id_from_response(data: dict[str, typing.Any]) -> str:
    """Return a session id from common API response shapes."""
    for key in ("id", "session_id"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    nested = data.get("session")
    if isinstance(nested, dict):
        for key in ("id", "session_id"):
            value = nested.get(key)
            if isinstance(value, str) and value:
                return value
    return ""
