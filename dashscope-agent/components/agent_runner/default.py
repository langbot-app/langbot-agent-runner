"""DashScope Agent default runner implementation.

Real Aliyun DashScope (百炼) API integration supporting agent and workflow app types.
"""

from __future__ import annotations

import logging
import typing

from langbot_plugin.api.definition.components.agent_runner.runner import AgentRunner
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentRunContext,
    AgentRunnerCapabilities,
    AgentRunResult,
)
from langbot_plugin.api.entities.builtin.provider.message import MessageChunk
from pkg.dashscope_client import (
    DashScopeAPIError,
    DashScopeClient,
    DashScopeConfigError,
    extract_references_from_chunk,
    replace_references,
)

logger = logging.getLogger(__name__)

# Thinking block markers (special Unicode characters used by DashScope)
THINK_START = "႑"
THINK_END = "႐"


class DefaultAgentRunner(AgentRunner):
    """Real AgentRunner for DashScope (阿里云百炼) API.

    Supports two app types:
    - agent: Agent with thinking/reasoning capability
    - workflow: Workflow execution with message format streaming

    Configuration (static, from ctx.config):
    - app-type: Application type (agent/workflow)
    - api-key: DashScope API key
    - app-id: DashScope application ID
    - references_quote: Prefix for reference text (default: "参考资料来自:")

    Runtime state (from ctx.state):
    - external.conversation_id: DashScope session_id for stateful sessions
    """

    @classmethod
    def get_capabilities(cls) -> AgentRunnerCapabilities:
        """Get runner capabilities."""
        return AgentRunnerCapabilities(
            streaming=True,
            stateful_session=True,
        )

    def _validate_config(self, ctx: AgentRunContext) -> dict[str, typing.Any]:
        """Validate and return static configuration.

        Raises DashScopeConfigError on missing required fields.
        """
        config = ctx.config or {}

        app_type = config.get("app-type", "agent")
        valid_types = ["agent", "workflow"]
        if app_type not in valid_types:
            raise DashScopeConfigError(
                f"Invalid app-type: {app_type}. Must be one of {valid_types}",
                code="dashscope.config_invalid",
            )

        api_key = config.get("api-key", "")
        if not api_key:
            raise DashScopeConfigError("api-key is required", code="dashscope.config_invalid")

        app_id = config.get("app-id", "")
        if not app_id:
            raise DashScopeConfigError("app-id is required", code="dashscope.config_invalid")

        return {
            "app_type": app_type,
            "api_key": api_key,
            "app_id": app_id,
            "references_quote": config.get("references_quote", "参考资料来自:"),
        }

    def _get_session_id(self, ctx: AgentRunContext) -> str:
        """Get session ID from state for multi-turn conversation.

        Priority:
        1. ctx.state.conversation["external.conversation_id"]
        2. Empty string (start new session)
        """
        external_conv_id = ctx.state.conversation.get("external.conversation_id")
        if external_conv_id:
            return external_conv_id
        return ""

    def _get_input_text(self, ctx: AgentRunContext) -> str:
        """Get text input from context."""
        return ctx.input.to_text()

    def _get_biz_params(self, ctx: AgentRunContext) -> dict[str, typing.Any]:
        """Get business parameters for workflow from ctx.params."""
        return dict(ctx.params or {})

    async def run(self, ctx: AgentRunContext) -> typing.AsyncGenerator[AgentRunResult, None]:
        """Run the DashScope agent.

        Streams AgentRunResult.message_delta chunks and final run_completed.
        """
        try:
            config = self._validate_config(ctx)
        except DashScopeConfigError as e:
            yield AgentRunResult.run_failed(
                error=e.message,
                code=e.code,
            )
            return

        client = DashScopeClient(
            api_key=config["api_key"],
            app_id=config["app_id"],
            app_type=config["app_type"],
            references_quote=config["references_quote"],
        )

        input_text = self._get_input_text(ctx)
        session_id = self._get_session_id(ctx)
        app_type = config["app_type"]

        try:
            if app_type == "workflow":
                async for result in self._run_workflow(ctx, client, input_text, session_id):
                    yield result
            else:
                async for result in self._run_agent(ctx, client, input_text, session_id):
                    yield result
        except DashScopeAPIError as e:
            yield AgentRunResult.run_failed(
                error=e.message,
                code=e.code,
            )
            return
        except Exception as e:
            logger.exception(f"DashScope runner unexpected error: {e}")
            yield AgentRunResult.run_failed(
                error=f"DashScope runner error: {e}",
                code="dashscope.unexpected_error",
            )
            return

    async def _run_agent(
        self,
        ctx: AgentRunContext,
        client: DashScopeClient,
        input_text: str,
        session_id: str,
    ) -> typing.AsyncGenerator[AgentRunResult, None]:
        """Run agent mode.

        Streams message_delta chunks with thinking content support.
        """
        pending_content = ""
        references_dict: dict[str, str] = {}
        final_session_id = session_id

        think_start = False
        think_end = False

        # Check if thinking should be enabled (default: True)
        # Can be controlled via ctx.params if needed
        enable_thinking = True

        # Use sync iterator since dashscope SDK is synchronous
        for chunk in client.call_agent(
            prompt=input_text,
            session_id=session_id,
            enable_thinking=enable_thinking,
        ):
            # Check for API errors
            status_code = chunk.get('status_code')
            if status_code != 200:
                raise DashScopeAPIError(
                    f"DashScope API error: status_code={status_code} "
                    f"message={chunk.get('message')} request_id={chunk.get('request_id')}",
                    code="dashscope.api_error",
                )

            if not chunk:
                continue

            stream_output = chunk.get('output', {})

            # Track session_id for stateful session
            if stream_output.get('session_id'):
                final_session_id = stream_output['session_id']

            # Handle thinking/reasoning content
            stream_think = stream_output.get('thoughts') or []
            if stream_think and stream_think[0].get('thought'):
                if not think_start:
                    think_start = True
                    pending_content += f'{THINK_START}\n{stream_think[0].get("thought")}'
                else:
                    # Continue outputting reasoning_content
                    pending_content += stream_think[0].get('thought')
            elif think_start and (not stream_think or stream_think[0].get('thought') == '') and not think_end:
                think_end = True
                pending_content += f'\n{THINK_END}\n'

            # Handle text content
            if stream_output.get('text') is not None:
                pending_content += stream_output.get('text')

            # Check if this is the final chunk
            finish_reason = stream_output.get('finish_reason')
            is_final = finish_reason != 'null' if finish_reason else False

            # Extract and accumulate references
            chunk_refs = extract_references_from_chunk(stream_output)
            references_dict.update(chunk_refs)

            # Replace references in content
            if references_dict:
                pending_content = replace_references(
                    pending_content,
                    references_dict,
                    client.references_quote,
                )

            # Yield periodically or on final chunk
            if pending_content:
                yield AgentRunResult.message_delta(
                    MessageChunk(
                        role="assistant",
                        content=pending_content,
                        is_final=is_final,
                    )
                )
                if is_final:
                    pending_content = ""

        # Update state with session_id for next run
        if final_session_id:
            yield AgentRunResult.state_updated(
                "external.conversation_id",
                final_session_id,
                scope="conversation",
            )

        yield AgentRunResult.run_completed()

    async def _run_workflow(
        self,
        ctx: AgentRunContext,
        client: DashScopeClient,
        input_text: str,
        session_id: str,
    ) -> typing.AsyncGenerator[AgentRunResult, None]:
        """Run workflow mode.

        Streams message_delta chunks from workflow message format output.
        """
        pending_content = ""
        references_dict: dict[str, str] = {}
        final_session_id = session_id

        # Get business parameters from context
        biz_params = self._get_biz_params(ctx)

        # Use sync iterator since dashscope SDK is synchronous
        for chunk in client.call_workflow(
            prompt=input_text,
            session_id=session_id,
            biz_params=biz_params,
        ):
            # Check for API errors
            status_code = chunk.get('status_code')
            if status_code != 200:
                raise DashScopeAPIError(
                    f"DashScope API error: status_code={status_code} "
                    f"message={chunk.get('message')} request_id={chunk.get('request_id')}",
                    code="dashscope.api_error",
                )

            if not chunk:
                continue

            stream_output = chunk.get('output', {})

            # Track session_id for stateful session
            if stream_output.get('session_id'):
                final_session_id = stream_output['session_id']

            # Handle workflow message format output
            workflow_message = stream_output.get('workflow_message')
            if workflow_message is not None:
                message_content = workflow_message.get('message', {})
                if message_content:
                    content = message_content.get('content', '')
                    if content:
                        pending_content += content

            # Check if this is the final chunk
            finish_reason = stream_output.get('finish_reason')
            is_final = finish_reason != 'null' if finish_reason else False

            # Extract and accumulate references
            chunk_refs = extract_references_from_chunk(stream_output)
            references_dict.update(chunk_refs)

            # Replace references in content
            if references_dict:
                pending_content = replace_references(
                    pending_content,
                    references_dict,
                    client.references_quote,
                )

            # Yield periodically or on final chunk
            if pending_content:
                yield AgentRunResult.message_delta(
                    MessageChunk(
                        role="assistant",
                        content=pending_content,
                        is_final=is_final,
                    )
                )
                if is_final:
                    pending_content = ""

        # Update state with session_id for next run
        if final_session_id:
            yield AgentRunResult.state_updated(
                "external.conversation_id",
                final_session_id,
                scope="conversation",
            )

        yield AgentRunResult.run_completed()
