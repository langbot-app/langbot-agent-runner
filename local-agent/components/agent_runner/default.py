"""Local Agent default runner implementation."""

from __future__ import annotations

import uuid
from typing import AsyncGenerator

from langbot_plugin.api.definition.components.agent_runner.runner import AgentRunner
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentRunContext,
    AgentRunnerCapabilities,
    AgentRunnerPermissions,
    AgentRunResult,
)
from langbot_plugin.api.entities.builtin.provider.message import Message, MessageChunk


class DefaultAgentRunner(AgentRunner):
    """Default AgentRunner for Local Agent.

    Minimal LLM runner implementation for MVP testing.
    Uses AgentRunAPIProxy for authorized resource access.
    """

    @classmethod
    def get_capabilities(cls) -> AgentRunnerCapabilities:
        """Get runner capabilities."""
        return AgentRunnerCapabilities(
            streaming=True,
            tool_calling=True,
            knowledge_retrieval=True,
            multimodal_input=True,
            stateful_session=True,
        )

    @classmethod
    def get_permissions(cls) -> AgentRunnerPermissions:
        """Get runner permissions for resource access."""
        return AgentRunnerPermissions(
            models=['invoke', 'stream'],
            tools=['list', 'detail', 'call'],
            knowledge_bases=['list', 'retrieve'],
        )

    async def run(
        self, ctx: AgentRunContext
    ) -> AsyncGenerator[AgentRunResult, None]:
        """Run the agent with minimal LLM streaming.

        Implementation matches original localagent runner behavior:
        1. Get authorized models from ctx.resources
        2. Build messages from config.prompt + ctx.messages + ctx.input
        3. Stream LLM response via invoke_llm_stream
        4. Accumulate content and yield every 8 chunks or on is_final
        5. Yield message_delta for streaming, run_completed on success
        """
        api = self.get_run_api(ctx)

        # Get authorized models
        models = api.get_allowed_models()
        if not models:
            yield AgentRunResult.run_failed(
                error="No authorized model for local-agent",
                code="runner.no_model",
            )
            return

        # TODO: Implement model fallback loop when primary fails
        primary_model = models[0]

        # Build messages for LLM
        messages = self._build_messages(ctx)

        try:
            # Stream LLM response with accumulation (matching original localagent behavior)
            accumulated_content = ''
            msg_idx = 0
            msg_sequence = 0

            async for chunk in api.invoke_llm_stream(primary_model.model_id, messages):
                msg_idx += 1

                # Accumulate content
                if chunk.content:
                    accumulated_content += chunk.content

                # Yield accumulated chunk every 8 chunks or on is_final
                # This matches original localagent runner behavior
                if msg_idx % 8 == 0 or chunk.is_final:
                    msg_sequence += 1
                    yield AgentRunResult.message_delta(
                        MessageChunk(
                            role='assistant',
                            content=accumulated_content,
                            is_final=chunk.is_final,
                            msg_sequence=msg_sequence,
                        )
                    )

            # Successful completion
            yield AgentRunResult.run_completed(finish_reason="stop")

        except Exception as e:
            yield AgentRunResult.run_failed(
                error=str(e),
                code="runner.llm_error",
                retryable=True,
            )

    def _build_messages(self, ctx: AgentRunContext) -> list[Message]:
        """Build messages list for LLM invocation.

        Structure:
        1. System prompt from config.prompt (if configured)
        2. Historical messages from ctx.messages (truncated by max-round)
        3. Current user input from ctx.input

        Args:
            ctx: Agent run context

        Returns:
            List of Message objects ready for LLM
        """
        messages: list[Message] = []

        # Add system prompt from config
        prompt_config = ctx.config.get("prompt", [])
        if prompt_config:
            for prompt_item in prompt_config:
                role = prompt_item.get("role", "system")
                content = prompt_item.get("content", "")
                if content:
                    messages.append(Message(role=role, content=content))

        # Get max-round for history truncation (use safe default if missing)
        max_round = ctx.config.get("max-round", 10)
        if max_round < 1:
            max_round = 10

        # Add historical messages (truncate if exceeds max-round)
        # Each round = 1 user + 1 assistant message
        # Keep last N rounds to avoid context overflow
        history_messages = ctx.messages
        if len(history_messages) > max_round * 2:
            # Keep last max_round*2 messages (max_round complete exchanges)
            history_messages = history_messages[-(max_round * 2):]

        messages.extend(history_messages)

        # Add current user input
        user_text = ctx.input.to_text()
        if user_text:
            messages.append(Message(role="user", content=user_text))

        # TODO: Handle multimodal input (images, files) from ctx.input.contents
        # TODO: Add knowledge base retrieval context before LLM call
        # TODO: Implement tool calling loop when LLM returns tool_calls

        return messages
