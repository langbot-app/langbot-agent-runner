"""Tbox Agent default runner implementation.

Real Tbox (蚂蚁百宝箱) API integration supporting chat with multimodal input and stateful sessions.
"""

from __future__ import annotations

import json
import logging
import typing

from pkg.tbox_client import (
    AsyncTboxClient,
    TboxAPIError,
    TboxConfigError,
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
    """Real AgentRunner for Tbox (蚂蚁百宝箱) API.

    Features:
    - Streaming and non-streaming responses
    - Multimodal input (image uploads)
    - Stateful session via conversation_id
    - Thinking content with ࿏...viewport tags

    Configuration (from ctx.config):
    - app-id: Tbox application ID
    - api-key: Tbox authorization token

    Runtime state (from ctx.state):
    - external.conversation_id: Tbox conversation ID for stateful sessions
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

        Raises TboxConfigError on missing required fields.
        """
        config = ctx.config or {}

        app_id = config.get("app-id", "")
        if not app_id:
            raise TboxConfigError("app-id is required", code="tbox.config_invalid")

        api_key = config.get("api-key", "")
        if not api_key:
            raise TboxConfigError("api-key is required", code="tbox.config_invalid")

        return {
            "app_id": app_id,
            "api_key": api_key,
        }

    def _get_user_id(self, ctx: AgentRunContext) -> str:
        """Get user identifier for Tbox API."""
        actor = ctx.actor
        if actor:
            return f"{actor.type}_{actor.id}"
        return f"user_{ctx.run_id}"

    def _get_external_conversation_id(self, ctx: AgentRunContext) -> str | None:
        """Get external conversation ID from state.

        Priority:
        1. ctx.state.conversation["external.conversation_id"]
        2. None (start new conversation)
        """
        # State (persistent external conversation ID)
        external_conv_id = ctx.state.conversation.get("external.conversation_id")
        if external_conv_id:
            return external_conv_id

        # Start new Tbox conversation
        return None

    async def _upload_input_files(
        self,
        ctx: AgentRunContext,
        client: AsyncTboxClient,
    ) -> list[dict[str, typing.Any]]:
        """Upload files from input attachments to Tbox.

        Returns list of Tbox file references.
        """
        uploaded_files: list[dict[str, typing.Any]] = []

        for attachment in ctx.input.attachments:
            try:
                file_bytes = attachment.content
                if not file_bytes:
                    continue

                file_name = attachment.name or "file"
                content_type = attachment.content_type or "application/octet-stream"

                # Tbox primarily supports images
                if content_type.startswith("image/"):
                    file_id = await client.upload_file(file_bytes, file_name)
                    if file_id:
                        uploaded_files.append({
                            "file_id": file_id,
                            "type": "image",
                        })
            except Exception as e:
                logger.warning(f"Failed to upload file {attachment.name}: {e}")
                # Continue without this file rather than failing the entire request

        return uploaded_files

    def _get_input_text(self, ctx: AgentRunContext) -> str:
        """Get text input from context."""
        return ctx.input.to_text()

    async def run(self, ctx: AgentRunContext) -> typing.AsyncGenerator[AgentRunResult, None]:
        """Run the Tbox agent.

        Streams AgentRunResult.message_delta chunks and final run_completed.
        """
        try:
            config = self._validate_config(ctx)
        except TboxConfigError as e:
            yield AgentRunResult.run_failed(
                error=e.message,
                code=e.code,
            )
            return

        client = AsyncTboxClient(api_key=config["api_key"])

        user_id = self._get_user_id(ctx)
        input_text = self._get_input_text(ctx)
        app_id = config["app_id"]

        # Upload files if present
        files = await self._upload_input_files(ctx, client)

        # Get conversation_id from state (not from config!)
        conversation_id = self._get_external_conversation_id(ctx)

        # Determine if streaming is supported
        # Note: Tbox streaming support might depend on the app configuration
        is_stream = True  # Default to streaming

        try:
            async for result in self._run_chat(
                ctx, client, app_id, user_id, input_text, conversation_id, files, is_stream
            ):
                yield result
        except TboxAPIError as e:
            yield AgentRunResult.run_failed(
                error=e.message,
                code=e.code,
            )
            return
        except Exception as e:
            logger.exception(f"Tbox runner unexpected error: {e}")
            yield AgentRunResult.run_failed(
                error=f"Tbox runner error: {e}",
                code="tbox.unexpected_error",
            )
            return

    async def _run_chat(
        self,
        ctx: AgentRunContext,
        client: AsyncTboxClient,
        app_id: str,
        user_id: str,
        input_text: str,
        conversation_id: str | None,
        files: list[dict[str, typing.Any]],
        is_stream: bool,
    ) -> typing.AsyncGenerator[AgentRunResult, None]:
        """Run chat with Tbox.

        Streams message_delta chunks and handles streaming/non-streaming responses.
        """
        pending_content = ""
        final_conversation_id = conversation_id
        has_response = False
        idx_msg = 0
        think_start = False
        think_end = False

        async for chunk in client.chat(
            app_id=app_id,
            user_id=user_id,
            query=input_text,
            stream=is_stream,
            conversation_id=conversation_id,
            files=files if files else None,
        ):
            chunk_type = chunk.get("type", "")

            if is_stream:
                # Handle streaming chunks
                if chunk_type == "chunk":
                    """
                    Tbox chunk structure:
                    {'lane': 'default', 'payload': {'conversationId': '...', 'messageId': '...', 'text': '...'}, 'type': 'chunk'}
                    """
                    # If thinking started but not ended, add closing tag
                    if think_start and not think_end:
                        pending_content += "\n viewport\n"
                        think_end = True

                    payload = chunk.get("payload", {})
                    if not final_conversation_id:
                        final_conversation_id = payload.get("conversationId")

                    if payload.get("text"):
                        idx_msg += 1
                        pending_content += payload.get("text")

                elif chunk_type == "thinking":
                    """
                    Tbox thinking chunk structure:
                    {'payload': '{"ext_data":{"text":"..."},"event":"flow.node.llm.thinking",...}', 'type': 'thinking'}
                    """
                    try:
                        payload = json.loads(chunk.get("payload", "{}"))
                        if payload.get("ext_data", {}).get("text"):
                            idx_msg += 1
                            content = payload.get("ext_data", {}).get("text")
                            if not think_start:
                                think_start = True
                                pending_content += f"<tool_call>\n{content}"
                            else:
                                pending_content += content
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse Tbox thinking payload: {chunk}")

                elif chunk_type == "error":
                    raise TboxAPIError(
                        f"Tbox API error: status_code={chunk.get('status_code')} "
                        f"message={chunk.get('message')} request_id={chunk.get('request_id')}",
                        code="tbox.api_error",
                    )

                # Yield periodic updates (every 8 chunks)
                if idx_msg > 0 and idx_msg % 8 == 0:
                    has_response = True
                    yield AgentRunResult.message_delta(
                        MessageChunk(
                            role="assistant",
                            content=pending_content,
                            is_final=False,
                        )
                    )

            else:
                # Handle non-streaming response
                """
                Tbox non-stream response:
                {'errorCode': '0', 'data': {'conversationId': '...', 'reasoningContent': [...], 'result': [...]}}
                """
                if chunk.get("errorCode") != "0":
                    raise TboxAPIError(
                        f"Tbox API request failed: {chunk.get('errorMsg', '')}",
                        code="tbox.api_error",
                    )

                payload = chunk.get("data", {})
                final_conversation_id = payload.get("conversationId", "")

                result = ""
                thinking_content = payload.get("reasoningContent", [])
                if thinking_content:
                    result += f"<tool_call>\n{thinking_content[0].get('text', '')}\n viewport\n"

                content = payload.get("result", [])
                if content:
                    result += content[0].get("chunk", "")

                has_response = True
                yield AgentRunResult.message_delta(
                    MessageChunk(
                        role="assistant",
                        content=result,
                        is_final=True,
                    )
                )

        # Handle remaining pending content for streaming
        if is_stream and pending_content:
            has_response = True
            yield AgentRunResult.message_delta(
                MessageChunk(
                    role="assistant",
                    content=pending_content,
                    is_final=True,
                )
            )

        if not has_response:
            raise TboxAPIError(
                "Tbox API returned no response",
                code="tbox.api_error",
            )

        # Update state with conversation_id for next run (scoped state)
        if final_conversation_id:
            yield AgentRunResult.state_updated(
                "external.conversation_id",
                final_conversation_id,
                scope="conversation",
            )

        yield AgentRunResult.run_completed()
