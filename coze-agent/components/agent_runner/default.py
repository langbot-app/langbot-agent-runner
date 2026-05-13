"""Coze Agent default runner implementation.

Real Coze API integration supporting streaming responses with multimodal input.
"""

from __future__ import annotations

import base64
import json
import logging
import typing

from pkg.coze_client import (
    AsyncCozeClient,
    CozeAPIError,
    CozeConfigError,
)
from langbot_plugin.api.definition.components.agent_runner.runner import AgentRunner
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentRunContext,
    AgentRunnerCapabilities,
    AgentRunResult,
)
from langbot_plugin.api.entities.builtin.provider.message import MessageChunk

logger = logging.getLogger(__name__)


class DefaultAgentRunner(AgentRunner):
    """Real AgentRunner for Coze API.

    Supports:
    - Streaming chat responses
    - Multimodal input (text, image, file)
    - Thinking/reasoning content with 🤔...💬 tags
    - Stateful session via conversation_id

    Configuration (static, from ctx.config):
    - bot-id: Coze Bot ID
    - api-key: Coze API Key
    - api-base: Coze API base URL (default: https://api.coze.cn)
    - timeout: Request timeout in seconds
    - auto-save-history: Whether to save chat history

    Runtime state (from ctx.state):
    - external.conversation_id: Coze conversation ID for stateful sessions
    """

    @classmethod
    def get_capabilities(cls) -> AgentRunnerCapabilities:
        """Get runner capabilities."""
        return AgentRunnerCapabilities(
            streaming=True,
            multimodal_input=True,
            stateful_session=True,
        )

    def _validate_config(self, ctx: AgentRunContext) -> dict[str, typing.Any]:
        """Validate and return static configuration.

        Raises CozeConfigError on missing required fields.
        """
        config = ctx.config or {}

        api_key = config.get("api-key", "")
        if not api_key:
            raise CozeConfigError("api-key is required", code="coze.config_invalid")

        bot_id = config.get("bot-id", "")
        if not bot_id:
            raise CozeConfigError("bot-id is required", code="coze.config_invalid")

        return {
            "api_key": api_key,
            "bot_id": bot_id,
            "api_base": config.get("api-base", "https://api.coze.cn"),
            "timeout": float(config.get("timeout", 120)),
            "auto_save_history": bool(config.get("auto-save-history", True)),
        }

    def _get_user_id(self, ctx: AgentRunContext) -> str:
        """Get user identifier for Coze API."""
        actor = ctx.actor
        if actor:
            return f"{actor.type}_{actor.id}"
        return f"user_{ctx.run_id}"

    def _get_external_conversation_id(self, ctx: AgentRunContext) -> str | None:
        """Get external conversation ID from state or context.

        Priority:
        1. ctx.state.conversation["external.conversation_id"]
        2. ctx.conversation.conversation_id
        3. None (start new conversation)
        """
        # Priority 1: State (persistent external conversation ID)
        external_conv_id = ctx.state.conversation.get("external.conversation_id")
        if external_conv_id:
            return external_conv_id

        # Priority 2: Context conversation ID (may be provided by host)
        if ctx.conversation and ctx.conversation.conversation_id:
            return ctx.conversation.conversation_id

        # Priority 3: None (start new Coze conversation)
        return None

    async def _build_additional_messages(
        self,
        ctx: AgentRunContext,
        client: AsyncCozeClient,
    ) -> list[dict[str, typing.Any]]:
        """Build Coze message format from input.

        Returns:
            List of Coze message objects with role, content, content_type
        """
        content_parts: list[dict[str, typing.Any]] = []

        # Process attachments first
        for attachment in ctx.input.attachments:
            try:
                # Handle image_base64 type
                if attachment.type == "image_base64":
                    # Upload image to get file_id
                    image_b64 = attachment.content
                    if isinstance(image_b64, str):
                        # Remove data URL prefix if present
                        if image_b64.startswith("data:"):
                            image_b64 = image_b64.split(",", 1)[1]
                        file_bytes = base64.b64decode(image_b64)
                    else:
                        file_bytes = image_b64

                    file_id = await client.upload_file(file_bytes, attachment.name or "image.png")
                    content_parts.append({"type": "image", "file_id": file_id})

                # Handle file type
                elif attachment.type == "file":
                    file_bytes = attachment.content
                    if file_bytes:
                        file_id = await client.upload_file(file_bytes, attachment.name or "file")
                        content_parts.append({"type": "file", "file_id": file_id})

                # Handle image type (direct bytes)
                elif attachment.type == "image":
                    file_bytes = attachment.content
                    if file_bytes:
                        file_id = await client.upload_file(file_bytes, attachment.name or "image.png")
                        content_parts.append({"type": "image", "file_id": file_id})

            except Exception as e:
                logger.warning(f"Failed to process attachment {attachment.name}: {e}")
                # Continue without this attachment

        # Add text content
        text = ctx.input.to_text()
        if text:
            content_parts.insert(0, {"type": "text", "text": text})

        if not content_parts:
            # Empty input, return empty messages list
            return []

        # Build message format
        if len(content_parts) == 1 and content_parts[0].get("type") == "text":
            # Simple text message
            return [
                {
                    "role": "user",
                    "content": content_parts[0].get("text", ""),
                    "content_type": "text",
                }
            ]
        else:
            # Multimodal message with object_string format
            return [
                {
                    "role": "user",
                    "content": json.dumps(content_parts),
                    "content_type": "object_string",
                }
            ]

    async def run(self, ctx: AgentRunContext) -> typing.AsyncGenerator[AgentRunResult, None]:
        """Run the Coze agent.

        Streams AgentRunResult.message_delta chunks and final run_completed.
        """
        try:
            config = self._validate_config(ctx)
        except CozeConfigError as e:
            yield AgentRunResult.run_failed(
                error=e.message,
                code=e.code,
            )
            return

        client = AsyncCozeClient(
            api_key=config["api_key"],
            api_base=config["api_base"],
            timeout=config["timeout"],
        )

        user_id = self._get_user_id(ctx)
        conversation_id = self._get_external_conversation_id(ctx)
        auto_save_history = config["auto_save_history"]
        bot_id = config["bot_id"]

        # Build additional messages from input
        try:
            additional_messages = await self._build_additional_messages(ctx, client)
        except Exception as e:
            logger.exception(f"Failed to build Coze messages: {e}")
            yield AgentRunResult.run_failed(
                error=f"Failed to prepare input: {e}",
                code="coze.input_error",
            )
            return

        final_conversation_id = conversation_id
        full_content = ""
        full_reasoning = ""
        has_response = False

        try:
            async for chunk in client.chat_messages(
                bot_id=bot_id,
                user_id=user_id,
                additional_messages=additional_messages,
                conversation_id=conversation_id,
                auto_save_history=auto_save_history,
            ):
                event_type = chunk.get("event", "")
                data = chunk.get("data", {})
                logger.debug(f"Coze event: {event_type}")

                if event_type == "conversation.message.delta":
                    # Collect reasoning content
                    if "reasoning_content" in data:
                        reasoning = data.get("reasoning_content", "")
                        if reasoning:
                            full_reasoning += reasoning

                    # Collect main content
                    if "content" in data:
                        content_delta = data.get("content", "")
                        if content_delta:
                            full_content += content_delta
                            has_response = True

                elif event_type.endswith(".done") or event_type == "conversation.chat.completed":
                    # Track conversation_id for stateful session
                    if data.get("conversation_id"):
                        final_conversation_id = data["conversation_id"]

                elif event_type == "error":
                    error_msg = data.get("message", "Unknown Coze API error")
                    yield AgentRunResult.run_failed(
                        error=f"Coze API error: {error_msg}",
                        code="coze.api_error",
                    )
                    return

            # Build final response content
            final_content = full_content

            # Add reasoning with think tags if present
            if full_reasoning:
                final_content = f"🤔\n{full_reasoning}\n💬\n{final_content}".strip()

            if not has_response:
                yield AgentRunResult.run_failed(
                    error="Coze API returned no response",
                    code="coze.empty_response",
                )
                return

            # Yield final message
            yield AgentRunResult.message_delta(
                MessageChunk(role="assistant", content=final_content, is_final=True)
            )

        except CozeAPIError as e:
            yield AgentRunResult.run_failed(
                error=e.message,
                code=e.code,
            )
            return
        except Exception as e:
            logger.exception(f"Coze runner unexpected error: {e}")
            yield AgentRunResult.run_failed(
                error=f"Coze runner error: {e}",
                code="coze.unexpected_error",
            )
            return
        finally:
            await client.close()

        # Update state with conversation_id for next run (scoped state)
        if final_conversation_id:
            yield AgentRunResult.state_updated(
                "external.conversation_id",
                final_conversation_id,
                scope="conversation",
            )

        yield AgentRunResult.run_completed()
