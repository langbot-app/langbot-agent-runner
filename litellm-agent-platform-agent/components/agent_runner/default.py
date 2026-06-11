"""LiteLLM Agent Platform default runner implementation."""

from __future__ import annotations

import json
import logging
import typing

from langbot_plugin.api.definition.components.agent_runner.runner import AgentRunner
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentRunContext,
    AgentRunResult,
)
from langbot_plugin.api.entities.builtin.provider.message import Message
from pkg.litellm_agent_platform_client import (
    AsyncLiteLLMAgentPlatformClient,
    LiteLLMAgentPlatformAPIError,
    LiteLLMAgentPlatformConfigError,
    session_id_from_response,
)

logger = logging.getLogger(__name__)


def _to_bool(value: typing.Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _resource_summary(ctx: AgentRunContext) -> dict[str, typing.Any]:
    return {
        "knowledge_bases": [
            {
                "kb_id": item.kb_id,
                "name": item.kb_name,
                "type": item.kb_type,
            }
            for item in ctx.resources.knowledge_bases
        ],
        "tools": [
            {
                "tool_name": item.tool_name,
                "type": item.tool_type,
                "description": item.description,
            }
            for item in ctx.resources.tools
        ],
    }


class DefaultAgentRunner(AgentRunner):
    """AgentRunner for the LiteLLM Agent Platform unified harness interface.

    Two HTTP targets are supported:

    - `agent-platform`: the full LiteLLM Agent Platform API
      (`/api/v1/managed_agents/...`).
    - `managed-agents-v0`: the lightweight lite-harness managed-agents server
      (`/v1/sessions` + event history).
    """

    def _validate_config(self, ctx: AgentRunContext) -> dict[str, typing.Any]:
        config = ctx.config or {}

        api_mode = str(config.get("api-mode", "agent-platform") or "agent-platform").strip()
        if api_mode not in {"agent-platform", "managed-agents-v0"}:
            raise LiteLLMAgentPlatformConfigError(
                "api-mode must be agent-platform or managed-agents-v0",
                code="litellm_agent_platform.config_invalid",
            )

        base_url = str(config.get("base-url", "") or "").strip().rstrip("/")
        if not base_url:
            raise LiteLLMAgentPlatformConfigError(
                "base-url is required",
                code="litellm_agent_platform.config_invalid",
            )

        agent_id = str(config.get("agent-id", "") or "").strip()
        if api_mode == "agent-platform" and not agent_id:
            raise LiteLLMAgentPlatformConfigError(
                "agent-id is required when api-mode=agent-platform",
                code="litellm_agent_platform.config_invalid",
            )

        harness = str(config.get("harness", "claude-code") or "claude-code").strip()
        if api_mode == "managed-agents-v0" and not harness:
            raise LiteLLMAgentPlatformConfigError(
                "harness is required when api-mode=managed-agents-v0",
                code="litellm_agent_platform.config_invalid",
            )

        timeout = float(config.get("timeout", 300) or 300)
        session_ready_timeout = float(config.get("session-ready-timeout", 300) or 300)
        poll_interval = float(config.get("poll-interval", 2) or 2)

        return {
            "api_mode": api_mode,
            "base_url": base_url,
            "api_key": str(config.get("api-key", "") or "").strip(),
            "agent_id": agent_id,
            "harness": harness,
            "model": str(config.get("model", "") or "").strip(),
            "title": str(config.get("title", "") or "").strip(),
            "create_session_if_missing": _to_bool(config.get("create-session-if-missing"), True),
            "session_ready_timeout": session_ready_timeout,
            "poll_interval": poll_interval,
            "timeout": timeout,
        }

    def _get_input_text(self, ctx: AgentRunContext) -> str:
        return ctx.input.to_text()

    def _with_langbot_run_scope_prompt(self, ctx: AgentRunContext, input_text: str) -> str:
        resources = json.dumps(
            _resource_summary(ctx),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return (
            "System instructions from LangBot:\n"
            f"- Current LangBot run_id: {ctx.run_id}\n"
            "- When calling the LangBot MCP gateway, pass this exact run_id in every tool call.\n"
            "- Do not invent, rewrite, or reuse a different run_id. If a LangBot MCP call is rejected, stop and report the error.\n"
            f"- Authorized LangBot resources for this run: {resources}\n\n"
            "User input:\n"
            f"{input_text}"
        )

    def _session_state_key(self, config: dict[str, typing.Any]) -> str:
        if config["api_mode"] == "managed-agents-v0":
            return "external.managed_session_id"
        return "external.session_id"

    def _get_stored_session_id(self, ctx: AgentRunContext, config: dict[str, typing.Any]) -> str:
        key = self._session_state_key(config)
        return str(ctx.state.conversation.get(key) or "").strip()

    def _session_title(self, config: dict[str, typing.Any], input_text: str) -> str:
        if config["title"]:
            return str(config["title"])
        return input_text.strip().replace("\n", " ")[:80] or "LangBot run"

    async def _get_or_create_platform_session(
        self,
        client: AsyncLiteLLMAgentPlatformClient,
        config: dict[str, typing.Any],
        stored_session_id: str,
        input_text: str,
    ) -> tuple[str, bool]:
        if stored_session_id:
            try:
                session = await client.get_platform_session(stored_session_id)
                status = str(session.get("status") or "")
                if status == "ready":
                    return stored_session_id, False
                if status not in {"failed", "dead", "stopped"}:
                    await client.wait_platform_session_ready(
                        stored_session_id,
                        timeout=config["session_ready_timeout"],
                        poll_interval=config["poll_interval"],
                    )
                    return stored_session_id, False
            except LiteLLMAgentPlatformAPIError:
                if not config["create_session_if_missing"]:
                    raise

        if not config["create_session_if_missing"]:
            raise LiteLLMAgentPlatformConfigError(
                "no stored platform session and create-session-if-missing is disabled",
                code="litellm_agent_platform.session_missing",
            )

        created = await client.create_platform_session(
            config["agent_id"],
            title=self._session_title(config, input_text),
        )
        session_id = session_id_from_response(created)
        if not session_id:
            raise LiteLLMAgentPlatformAPIError(
                f"LiteLLM Agent Platform did not return a session id: {created!r}",
                code="litellm_agent_platform.response_invalid",
            )
        await client.wait_platform_session_ready(
            session_id,
            timeout=config["session_ready_timeout"],
            poll_interval=config["poll_interval"],
        )
        return session_id, True

    async def _get_or_create_managed_session(
        self,
        client: AsyncLiteLLMAgentPlatformClient,
        config: dict[str, typing.Any],
        stored_session_id: str,
    ) -> tuple[str, bool]:
        if stored_session_id:
            try:
                await client.get_managed_session(stored_session_id)
                return stored_session_id, False
            except LiteLLMAgentPlatformAPIError:
                if not config["create_session_if_missing"]:
                    raise

        if not config["create_session_if_missing"]:
            raise LiteLLMAgentPlatformConfigError(
                "no stored managed session and create-session-if-missing is disabled",
                code="litellm_agent_platform.session_missing",
            )

        created = await client.create_managed_session(config["harness"], config["model"])
        session_id = session_id_from_response(created)
        if not session_id:
            raise LiteLLMAgentPlatformAPIError(
                f"lite-harness managed-agents did not return a session id: {created!r}",
                code="litellm_agent_platform.response_invalid",
            )
        return session_id, True

    async def run(self, ctx: AgentRunContext) -> typing.AsyncGenerator[AgentRunResult, None]:
        """Run through LiteLLM Agent Platform and return the assistant message."""
        try:
            config = self._validate_config(ctx)
        except LiteLLMAgentPlatformConfigError as e:
            yield AgentRunResult.run_failed(ctx.run_id, error=e.message, code=e.code)
            return

        input_text = self._get_input_text(ctx)
        if not input_text:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error="input text is required",
                code="litellm_agent_platform.empty_input",
            )
            return
        request_text = self._with_langbot_run_scope_prompt(ctx, input_text)

        client = AsyncLiteLLMAgentPlatformClient(
            base_url=config["base_url"],
            api_key=config["api_key"],
            timeout=config["timeout"],
        )

        try:
            stored_session_id = self._get_stored_session_id(ctx, config)
            if config["api_mode"] == "managed-agents-v0":
                session_id, created = await self._get_or_create_managed_session(client, config, stored_session_id)
                content, _events = await client.send_managed_message_and_wait(
                    session_id,
                    request_text,
                    timeout=config["timeout"],
                    poll_interval=config["poll_interval"],
                )
            else:
                session_id, created = await self._get_or_create_platform_session(
                    client,
                    config,
                    stored_session_id,
                    input_text,
                )
                content, _messages = await client.send_platform_message_and_wait(
                    session_id,
                    request_text,
                    model=config["model"],
                    timeout=config["timeout"],
                    poll_interval=config["poll_interval"],
                )

            if not content:
                yield AgentRunResult.run_failed(
                    ctx.run_id,
                    error="LiteLLM Agent Platform returned no assistant text",
                    code="litellm_agent_platform.empty_response",
                )
                return

            yield AgentRunResult.message_completed(
                ctx.run_id,
                Message(role="assistant", content=content),
            )
            state_key = self._session_state_key(config)
            if created or not stored_session_id or stored_session_id != session_id:
                yield AgentRunResult.state_updated(
                    ctx.run_id,
                    state_key,
                    session_id,
                    scope="conversation",
                )
                if config["api_mode"] == "managed-agents-v0":
                    yield AgentRunResult.state_updated(
                        ctx.run_id,
                        "external.session_id",
                        session_id,
                        scope="conversation",
                    )
            yield AgentRunResult.run_completed(ctx.run_id, finish_reason="stop")

        except LiteLLMAgentPlatformConfigError as e:
            yield AgentRunResult.run_failed(ctx.run_id, error=e.message, code=e.code)
            return
        except LiteLLMAgentPlatformAPIError as e:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=e.message,
                code=e.code,
                retryable=e.retryable,
            )
            return
        except Exception as e:
            logger.exception("LiteLLM Agent Platform runner unexpected error: %s", e)
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"LiteLLM Agent Platform runner error: {e}",
                code="litellm_agent_platform.unexpected_error",
            )
            return
