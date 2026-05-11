"""Dify Agent default runner implementation (Phase 1).

Real Dify Service API integration supporting chat, agent, and workflow app types.
"""

from __future__ import annotations

import json
import logging
import typing
import uuid

from pkg.dify_client import (
    AsyncDifyClient,
    DifyAPIError,
    DifyConfigError,
    extract_text_from_output,
    process_thinking_content,
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
    """Real AgentRunner for Dify Service API.

    Supports three app types:
    - chat: Chat assistant (including Chatflow)
    - agent: Agent with tool calls
    - workflow: Workflow execution

    Configuration (static, from ctx.config):
    - base-url: Dify API base URL (default: https://api.dify.ai/v1)
    - app-type: Application type (chat/agent/workflow)
    - api-key: Dify API key
    - base-prompt: Default prompt when input is empty
    - timeout: Request timeout in seconds
    - remove-think: Whether to remove thinking tags from output

    Runtime state (from ctx.state):
    - external.conversation_id: Dify conversation ID for stateful sessions

    Runtime params (from ctx.params):
    - Workflow inputs passed to Dify workflow endpoint
    - Custom variables passed to Dify chat-messages inputs
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

        Raises DifyConfigError on missing required fields.
        """
        config = ctx.config or {}

        base_url = config.get("base-url", "https://api.dify.ai/v1")
        if not base_url:
            raise DifyConfigError("base-url is required", code="dify.config_invalid")

        api_key = config.get("api-key", "")
        if not api_key:
            raise DifyConfigError("api-key is required", code="dify.config_invalid")

        app_type = config.get("app-type", "chat")
        valid_types = ["chat", "agent", "workflow"]
        if app_type not in valid_types:
            raise DifyConfigError(
                f"Invalid app-type: {app_type}. Must be one of {valid_types}",
                code="dify.config_invalid",
            )

        return {
            "base_url": base_url,
            "api_key": api_key,
            "app_type": app_type,
            "base_prompt": config.get("base-prompt", ""),
            "timeout": float(config.get("timeout", 30)),
            "remove_think": bool(config.get("remove-think", False)),
        }

    def _get_user_tag(self, ctx: AgentRunContext) -> str:
        """Get user identifier for Dify API."""
        actor = ctx.actor
        if actor:
            return f"{actor.type}_{actor.id}"
        return f"user_{ctx.run_id}"

    def _get_external_conversation_id(self, ctx: AgentRunContext) -> str:
        """Get external conversation ID from state or context.

        Priority:
        1. ctx.state.conversation["external.conversation_id"]
        2. ctx.conversation.conversation_id
        3. Empty string (start new conversation)
        """
        # Priority 1: State (persistent external conversation ID)
        external_conv_id = ctx.state.conversation.get("external.conversation_id")
        if external_conv_id:
            return external_conv_id

        # Priority 2: Context conversation ID (may be provided by host)
        if ctx.conversation and ctx.conversation.conversation_id:
            return ctx.conversation.conversation_id

        # Priority 3: Empty (start new Dify conversation)
        return ""

    def _get_dify_inputs(self, ctx: AgentRunContext) -> dict[str, typing.Any]:
        """Get inputs for Dify API from ctx.params.

        Does NOT modify ctx.params.
        """
        return dict(ctx.params or {})

    async def _upload_input_files(
        self,
        ctx: AgentRunContext,
        client: AsyncDifyClient,
        user: str,
    ) -> list[dict[str, typing.Any]]:
        """Upload files from input attachments to Dify.

        Returns list of Dify file references.
        """
        uploaded_files: list[dict[str, typing.Any]] = []

        for attachment in ctx.input.attachments:
            try:
                file_bytes = attachment.content
                if not file_bytes:
                    continue

                file_name = attachment.name or "file"
                content_type = attachment.content_type or "application/octet-stream"

                # Determine Dify file type from content type
                if content_type.startswith("image/"):
                    file_type = "image"
                elif content_type.startswith("audio/"):
                    file_type = "audio"
                elif content_type.startswith("video/"):
                    file_type = "video"
                else:
                    file_type = "document"

                result = await client.upload_file(file_name, file_bytes, content_type, user)
                file_id = result.get("id")

                if file_id:
                    uploaded_files.append({
                        "type": file_type,
                        "transfer_method": "local_file",
                        "upload_file_id": file_id,
                    })
            except Exception as e:
                logger.warning(f"Failed to upload file {attachment.name}: {e}")
                # Continue without this file rather than failing the entire request

        return uploaded_files

    def _get_input_text(self, ctx: AgentRunContext, base_prompt: str) -> str:
        """Get text input, fallback to base_prompt if empty."""
        text = ctx.input.to_text()
        if not text:
            return base_prompt
        return text

    async def run(self, ctx: AgentRunContext) -> typing.AsyncGenerator[AgentRunResult, None]:
        """Run the Dify agent.

        Streams AgentRunResult.message_delta chunks and final run_completed.
        """
        try:
            config = self._validate_config(ctx)
        except DifyConfigError as e:
            yield AgentRunResult.run_failed(
                error=e.message,
                code=e.code,
            )
            return

        client = AsyncDifyClient(
            api_key=config["api_key"],
            base_url=config["base_url"],
            timeout=config["timeout"],
        )

        user = self._get_user_tag(ctx)
        input_text = self._get_input_text(ctx, config["base_prompt"])
        remove_think = config["remove_think"]

        # Upload files if present
        files = await self._upload_input_files(ctx, client, user)

        # Get inputs from params (read-only, do not modify)
        inputs = self._get_dify_inputs(ctx)

        # Get conversation_id from state (not from config!)
        conversation_id = self._get_external_conversation_id(ctx)

        app_type = config["app_type"]

        try:
            if app_type == "workflow":
                # Workflow mode - uses different endpoint
                async for result in self._run_workflow(
                    ctx, client, inputs, input_text, user, files, remove_think
                ):
                    yield result
            else:
                # Chat or Agent mode - uses chat-messages endpoint
                async for result in self._run_chat_or_agent(
                    ctx, client, inputs, input_text, user, conversation_id, files, app_type, remove_think
                ):
                    yield result
        except DifyAPIError as e:
            yield AgentRunResult.run_failed(
                error=e.message,
                code=e.code,
            )
            return
        except Exception as e:
            logger.exception(f"Dify runner unexpected error: {e}")
            yield AgentRunResult.run_failed(
                error=f"Dify runner error: {e}",
                code="dify.unexpected_error",
            )
            return

    async def _run_chat_or_agent(
        self,
        ctx: AgentRunContext,
        client: AsyncDifyClient,
        inputs: dict[str, typing.Any],
        input_text: str,
        user: str,
        conversation_id: str,
        files: list[dict[str, typing.Any]],
        app_type: str,
        remove_think: bool,
    ) -> typing.AsyncGenerator[AgentRunResult, None]:
        """Run chat or agent mode.

        Streams message_delta chunks and handles agent-specific events.
        """
        pending_content = ""
        mode = "basic"  # basic or workflow mode in chat
        has_response = False
        final_conversation_id = conversation_id

        async for event in client.chat_messages(
            inputs=inputs,
            query=input_text,
            user=user,
            conversation_id=conversation_id,
            files=files,
        ):
            event_type = event.get("event", "")
            logger.debug(f"Dify {app_type} event: {event_type}")

            if event_type == "workflow_started":
                mode = "workflow"

            if event_type == "error":
                raise DifyAPIError(
                    f"Dify API error: {event.get('message', 'Unknown error')}",
                    code="dify.api_error",
                )

            # Track conversation_id for stateful session
            if event.get("conversation_id"):
                final_conversation_id = event["conversation_id"]

            # Handle different event types based on app_type and mode
            if mode == "workflow" and event_type == "node_finished":
                if event.get("data", {}).get("node_type") == "answer":
                    answer = extract_text_from_output(
                        event.get("data", {}).get("outputs", {}).get("answer")
                    )
                    content, _ = process_thinking_content(answer, remove_think)
                    if content:
                        has_response = True
                        yield AgentRunResult.message_delta(
                            MessageChunk(role="assistant", content=content)
                        )

            elif event_type == "message" or event_type == "agent_message":
                # Accumulate text chunks
                answer = event.get("answer", "")
                pending_content += answer

            elif event_type == "message_end":
                # Final message for chat mode
                if pending_content:
                    content, _ = process_thinking_content(pending_content, remove_think)
                    has_response = True
                    yield AgentRunResult.message_delta(
                        MessageChunk(role="assistant", content=content, is_final=True)
                    )
                pending_content = ""

            elif event_type == "agent_thought" and app_type == "agent":
                # Agent thought events - handle tool calls
                tool = event.get("tool", "")
                observation = event.get("observation", "")

                # Skip tool result observations
                if tool and observation:
                    continue

                # Yield accumulated content before tool call
                if pending_content:
                    content, _ = process_thinking_content(pending_content, remove_think)
                    if content:
                        has_response = True
                        yield AgentRunResult.message_delta(
                            MessageChunk(role="assistant", content=content)
                        )
                    pending_content = ""

                # Report tool call as message_delta with tool_calls
                if tool:
                    yield AgentRunResult.message_delta(
                        MessageChunk(
                            role="assistant",
                            content="",
                            tool_calls=[
                                {
                                    "id": event.get("id", str(uuid.uuid4())),
                                    "type": "function",
                                    "function": {
                                        "name": tool,
                                        "arguments": json.dumps({}),
                                    },
                                }
                            ],
                        )
                    )

            elif event_type == "message_file":
                # Handle image/file output from agent
                if event.get("type") == "image" and event.get("belongs_to") == "assistant":
                    image_url = event.get("url", "")
                    if image_url:
                        # Handle relative URLs
                        if not image_url.startswith("http"):
                            base_url = client.base_url
                            if base_url.endswith("/v1"):
                                base_url = base_url[:-3]
                            image_url = base_url + image_url

                        has_response = True
                        yield AgentRunResult.message_delta(
                            MessageChunk(
                                role="assistant",
                                content=[
                                    {"type": "image_url", "image_url": {"url": image_url}}
                                ],
                            )
                        )

            elif event_type == "workflow_finished":
                mode = "workflow"
                data = event.get("data", {})
                if data.get("error"):
                    raise DifyAPIError(f"Dify workflow error: {data['error']}", code="dify.api_error")

        # Handle any remaining pending content
        if pending_content:
            content, _ = process_thinking_content(pending_content, remove_think)
            if content:
                has_response = True
                yield AgentRunResult.message_delta(
                    MessageChunk(role="assistant", content=content, is_final=True)
                )

        if not has_response:
            raise DifyAPIError(
                "Dify API returned no response",
                code="dify.api_error",
            )

        # Update state with conversation_id for next run (scoped state)
        if final_conversation_id:
            yield AgentRunResult.state_updated(
                "external.conversation_id",
                final_conversation_id,
                scope="conversation",
            )

        yield AgentRunResult.run_completed()

    async def _run_workflow(
        self,
        ctx: AgentRunContext,
        client: AsyncDifyClient,
        inputs: dict[str, typing.Any],
        input_text: str,
        user: str,
        files: list[dict[str, typing.Any]],
        remove_think: bool,
    ) -> typing.AsyncGenerator[AgentRunResult, None]:
        """Run workflow mode.

        Streams message_delta chunks and handles workflow-specific events.

        Workflow legacy inputs are derived from context:
        - langbot_user_message_text: input_text
        - langbot_session_id: ctx.conversation.session_id or ctx.run_id
        - langbot_conversation_id: from state or ctx.conversation
        - langbot_msg_create_time: ctx.params.get("msg_create_time")
        """
        # Derive legacy input variables from context (Dify-specific, not SDK protocol)
        session_id = ctx.conversation.session_id if ctx.conversation else None
        session_id = session_id or ctx.run_id

        # Get conversation_id from state or context for legacy compatibility
        legacy_conv_id = ctx.state.conversation.get("external.conversation_id")
        if not legacy_conv_id and ctx.conversation:
            legacy_conv_id = ctx.conversation.conversation_id
        if not legacy_conv_id:
            legacy_conv_id = ctx.run_id

        msg_create_time = inputs.get("msg_create_time")

        workflow_inputs = {
            "langbot_user_message_text": input_text,
            "langbot_session_id": session_id,
            "langbot_conversation_id": legacy_conv_id,
        }
        if msg_create_time:
            workflow_inputs["langbot_msg_create_time"] = msg_create_time

        # Merge with user params (user params take precedence)
        workflow_inputs.update(inputs)

        pending_content = ""
        has_response = False
        ignored_events = ["workflow_started"]

        async for event in client.workflow_run(
            inputs=workflow_inputs,
            user=user,
            files=files,
        ):
            event_type = event.get("event", "")
            logger.debug(f"Dify workflow event: {event_type}")

            if event_type == "error":
                raise DifyAPIError(
                    f"Dify workflow error: {event.get('message', 'Unknown error')}",
                    code="dify.api_error",
                )

            if event_type in ignored_events:
                continue

            if event_type == "node_started":
                data = event.get("data", {})
                node_type = data.get("node_type", "")
                if node_type in ["start", "end"]:
                    continue

                # Report node start as tool call indicator
                yield AgentRunResult.message_delta(
                    MessageChunk(
                        role="assistant",
                        content="",
                        tool_calls=[
                            {
                                "id": data.get("node_id", str(uuid.uuid4())),
                                "type": "function",
                                "function": {
                                    "name": data.get("title", node_type),
                                    "arguments": json.dumps({}),
                                },
                            }
                        ],
                    )
                )

            elif event_type == "text_chunk":
                # Streaming text output from workflow
                text = event.get("data", {}).get("text", "")
                pending_content += text

            elif event_type == "workflow_finished":
                data = event.get("data", {})
                if data.get("error"):
                    raise DifyAPIError(f"Dify workflow error: {data['error']}", code="dify.api_error")

                # Get final output
                summary = extract_text_from_output(data.get("outputs", {}).get("summary", ""))
                if summary:
                    content, _ = process_thinking_content(summary, remove_think)
                    has_response = True
                    yield AgentRunResult.message_delta(
                        MessageChunk(role="assistant", content=content, is_final=True)
                    )

        # Handle remaining pending content
        if pending_content:
            content, _ = process_thinking_content(pending_content, remove_think)
            if content:
                has_response = True
                yield AgentRunResult.message_delta(
                    MessageChunk(role="assistant", content=content, is_final=True)
                )

        if not has_response:
            raise DifyAPIError(
                "Dify workflow returned no response",
                code="dify.api_error",
            )

        yield AgentRunResult.run_completed()
