"""Langflow Agent default runner implementation.

Real Langflow API integration supporting flow execution with streaming and non-streaming modes.
"""

from __future__ import annotations

import json
import logging
import typing
import uuid

from langbot_plugin.api.definition.components.agent_runner.runner import AgentRunner
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentRunContext,
    AgentRunResult,
)
from langbot_plugin.api.entities.builtin.provider.message import Message, MessageChunk
from pkg.langflow_client import (
    AsyncLangflowClient,
    LangflowAPIError,
    LangflowConfigError,
    extract_message_from_response,
)

logger = logging.getLogger(__name__)


class DefaultAgentRunner(AgentRunner):
    """Real AgentRunner for Langflow API.

    Supports running Langflow flows via the /api/v1/run/{flow_id} endpoint.

    Configuration (static, from ctx.config):
    - base-url: Langflow API base URL (default: http://localhost:7860)
    - api-key: Langflow API key
    - flow-id: The flow ID to run
    - input-type: Input type for the flow (default: chat)
    - output-type: Output type for the flow (default: chat)
    - tweaks: JSON tweaks configuration (default: {})

    Runtime state (from ctx.state):
    - external.session_id: Langflow session ID for stateful sessions
    """

    def _validate_config(self, ctx: AgentRunContext) -> dict[str, typing.Any]:
        """Validate and return static configuration.

        Raises LangflowConfigError on missing required fields.
        """
        config = ctx.config or {}

        base_url = config.get("base-url", "http://localhost:7860")
        if not base_url:
            raise LangflowConfigError("base-url is required", code="langflow.config_invalid")

        api_key = config.get("api-key", "")
        if not api_key:
            raise LangflowConfigError("api-key is required", code="langflow.config_invalid")

        flow_id = config.get("flow-id", "")
        if not flow_id:
            raise LangflowConfigError("flow-id is required", code="langflow.config_invalid")

        # Parse tweaks from JSON string if needed
        tweaks_raw = config.get("tweaks", "{}")
        if isinstance(tweaks_raw, str):
            try:
                tweaks = json.loads(tweaks_raw) if tweaks_raw.strip() else {}
            except json.JSONDecodeError:
                logger.warning(f"Invalid tweaks JSON: {tweaks_raw}, using empty dict")
                tweaks = {}
        elif isinstance(tweaks_raw, dict):
            tweaks = tweaks_raw
        else:
            tweaks = {}

        return {
            "base_url": base_url,
            "api_key": api_key,
            "flow_id": flow_id,
            "input_type": config.get("input-type", "chat"),
            "output_type": config.get("output-type", "chat"),
            "tweaks": tweaks,
            "timeout": float(config.get("timeout", 120)),
        }

    def _get_session_id(self, ctx: AgentRunContext) -> str:
        """Get or generate session ID for Langflow.

        Priority:
        1. ctx.state.conversation["external.session_id"]
        2. Generate new UUID

        Returns:
            Session ID string
        """
        # Check for existing session in state
        session_id = ctx.state.conversation.get("external.session_id")
        if session_id:
            return session_id

        # Generate new session ID
        return str(uuid.uuid4())

    def _get_user_tag(self, ctx: AgentRunContext) -> str:
        """Get user identifier for logging."""
        actor = ctx.actor
        if actor and actor.actor_id:
            return f"{actor.actor_type}_{actor.actor_id}"
        return f"user_{ctx.run_id}"

    def _should_stream(self, ctx: AgentRunContext) -> bool:
        """Decide whether to request streaming from Langflow."""
        configured = ctx.config.get("streaming")
        if configured is not None:
            return bool(configured)
        return bool(ctx.runtime.metadata.get("streaming_supported", True))

    async def run(self, ctx: AgentRunContext) -> typing.AsyncGenerator[AgentRunResult, None]:
        """Run the Langflow flow.

        Streams AgentRunResult.message_delta chunks for streaming,
        or yields message_completed for non-streaming.
        """
        try:
            config = self._validate_config(ctx)
        except LangflowConfigError as e:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=e.message,
                code=e.code,
            )
            return

        client = AsyncLangflowClient(
            api_key=config["api_key"],
            base_url=config["base_url"],
            timeout=config["timeout"],
        )

        input_text = ctx.input.to_text()
        session_id = self._get_session_id(ctx)

        is_stream = self._should_stream(ctx)

        try:
            accumulated_content = ""
            message_count = 0
            has_response = False
            final_session_id = session_id

            async for data in client.run_flow(
                flow_id=config["flow_id"],
                input_value=input_text,
                input_type=config["input_type"],
                output_type=config["output_type"],
                tweaks=config["tweaks"],
                session_id=session_id,
                stream=is_stream,
            ):
                # Extract message content from response
                message_text = extract_message_from_response(data)

                if message_text:
                    if is_stream:
                        # For streaming, accumulate and yield chunks
                        accumulated_content = message_text
                        message_count += 1

                        # Yield chunks periodically (every 8 events or when content changes significantly)
                        if message_count % 8 == 0 or len(message_text) > 0:
                            yield AgentRunResult.message_delta(
                                ctx.run_id,
                                MessageChunk(
                                    role="assistant",
                                    content=accumulated_content,
                                    is_final=False,
                                ),
                            )
                            has_response = True
                    else:
                        # For non-streaming, just accumulate
                        accumulated_content = message_text

                # Track session_id from response if present
                if "session_id" in data:
                    final_session_id = data["session_id"]

            # Final output
            if accumulated_content:
                if is_stream:
                    yield AgentRunResult.message_delta(
                        ctx.run_id,
                        MessageChunk(
                            role="assistant",
                            content=accumulated_content,
                            is_final=True,
                        ),
                    )
                else:
                    # Non-streaming: return complete message
                    message = Message(
                        role="assistant",
                        content=accumulated_content,
                    )
                    yield AgentRunResult.message_completed(ctx.run_id, message)
                has_response = True

            if not has_response:
                raise LangflowAPIError(
                    "Langflow API returned no response",
                    code="langflow.api_error",
                )

            # Update state with session_id for next run
            if final_session_id:
                yield AgentRunResult.state_updated(
                    ctx.run_id,
                    "external.session_id",
                    final_session_id,
                    scope="conversation",
                )

            yield AgentRunResult.run_completed(ctx.run_id)

        except LangflowAPIError as e:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=e.message,
                code=e.code,
            )
            return
        except Exception as e:
            logger.exception(f"Langflow runner unexpected error: {e}")
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"Langflow runner error: {e}",
                code="langflow.unexpected_error",
            )
            return
