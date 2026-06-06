"""n8n Workflow Agent default runner implementation.

Real n8n webhook integration supporting streaming and non-streaming responses.
"""

from __future__ import annotations

import logging
import typing
import uuid

from langbot_plugin.api.definition.components.agent_runner.runner import AgentRunner
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentRunContext,
    AgentRunnerCapabilities,
    AgentRunResult,
)
from langbot_plugin.api.entities.builtin.provider.message import MessageChunk
from pkg.n8n_client import (
    AsyncN8nClient,
    N8nAPIError,
    N8nConfigError,
)

logger = logging.getLogger(__name__)


def _get_adapter_params(ctx: AgentRunContext) -> dict[str, typing.Any]:
    """Read single-run business params from adapter.extra.params."""
    if ctx.adapter is None:
        return {}
    params = (ctx.adapter.extra or {}).get("params")
    return dict(params) if isinstance(params, dict) else {}


class DefaultAgentRunner(AgentRunner):
    """Real AgentRunner for n8n Webhook.

    Supports:
    - Webhook calls with various authentication types (none, basic, jwt, header)
    - Streaming response (type: item/end format)
    - Non-streaming JSON response
    - Stateful session via conversation_id

    Configuration (static, from ctx.config):
    - webhook-url: n8n webhook URL (required)
    - auth-type: Authentication type (none/basic/jwt/header)
    - basic-username: Username for basic auth
    - basic-password: Password for basic auth
    - jwt-secret: Secret key for JWT auth
    - jwt-algorithm: JWT algorithm (default: HS256)
    - header-name: Custom header name for header auth
    - header-value: Custom header value for header auth
    - timeout: Request timeout in seconds (default: 120)
    - output-key: Key to extract from non-streaming JSON response (default: response)

    Runtime state (from ctx.state):
    - external.conversation_id: n8n conversation ID for stateful sessions
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

        Raises N8nConfigError on missing required fields.
        """
        config = ctx.config or {}

        webhook_url = config.get("webhook-url", "")
        if not webhook_url:
            raise N8nConfigError("webhook-url is required", code="n8n.config_invalid")

        auth_type = config.get("auth-type", "none")
        valid_auth_types = ["none", "basic", "jwt", "header"]
        if auth_type not in valid_auth_types:
            raise N8nConfigError(
                f"Invalid auth-type: {auth_type}. Must be one of {valid_auth_types}",
                code="n8n.config_invalid",
            )

        return {
            "webhook_url": webhook_url,
            "auth_type": auth_type,
            "auth_config": {
                "basic_username": config.get("basic-username", ""),
                "basic_password": config.get("basic-password", ""),
                "jwt_secret": config.get("jwt-secret", ""),
                "jwt_algorithm": config.get("jwt-algorithm", "HS256"),
                "header_name": config.get("header-name", ""),
                "header_value": config.get("header-value", ""),
            },
            "timeout": float(config.get("timeout", 120)),
            "output_key": config.get("output-key", "response"),
        }

    def _get_user_tag(self, ctx: AgentRunContext) -> str:
        """Get user identifier for n8n webhook."""
        actor = ctx.actor
        if actor and actor.actor_id:
            return f"{actor.actor_type}_{actor.actor_id}"
        return f"user_{ctx.run_id}"

    def _get_conversation_id(self, ctx: AgentRunContext) -> str:
        """Get or create conversation ID.

        Priority:
        1. ctx.state.conversation["external.conversation_id"] (persistent)
        2. ctx.conversation.conversation_id (from host)
        3. Generate new UUID

        Returns the conversation ID to use for the webhook call.
        """
        # Priority 1: State (persistent external conversation ID)
        external_conv_id = ctx.state.conversation.get("external.conversation_id")
        if external_conv_id:
            return external_conv_id

        # Priority 2: Context conversation ID
        if ctx.conversation and ctx.conversation.conversation_id:
            return ctx.conversation.conversation_id

        # Priority 3: Generate new conversation ID
        return str(uuid.uuid4())

    def _get_session_id(self, ctx: AgentRunContext) -> str:
        """Get session ID from context."""
        if ctx.conversation and ctx.conversation.session_id:
            return ctx.conversation.session_id
        return ctx.run_id

    def _build_payload(
        self,
        ctx: AgentRunContext,
        user_message: str,
        conversation_id: str,
    ) -> dict[str, typing.Any]:
        """Build webhook payload.

        Includes standard fields and merges adapter params.
        """
        user_tag = self._get_user_tag(ctx)
        session_id = self._get_session_id(ctx)
        params = _get_adapter_params(ctx)

        payload = {
            # Standard message fields (multiple keys for compatibility)
            "chatInput": user_message,
            "message": user_message,
            "user_message_text": user_message,
            # Session/conversation tracking
            "conversation_id": conversation_id,
            "session_id": session_id,
            "user_id": user_tag,
        }

        # Add optional fields from adapter params
        if params:
            # msg_create_time is commonly used
            msg_create_time = params.get("msg_create_time")
            if msg_create_time:
                payload["msg_create_time"] = msg_create_time

            # Merge other params (excluding reserved keys)
            reserved_keys = {"msg_create_time", "conversation_id", "session_id", "user_id"}
            for key, value in params.items():
                if key not in reserved_keys:
                    payload[key] = value

        return payload

    async def run(self, ctx: AgentRunContext) -> typing.AsyncGenerator[AgentRunResult, None]:
        """Run the n8n webhook.

        Streams AgentRunResult.message_delta chunks and final run_completed.
        """
        try:
            config = self._validate_config(ctx)
        except N8nConfigError as e:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=e.message,
                code=e.code,
            )
            return

        client = AsyncN8nClient(
            webhook_url=config["webhook_url"],
            timeout=config["timeout"],
            output_key=config["output_key"],
        )

        # Get text input
        user_message = ctx.input.to_text()

        # Get or create conversation ID
        conversation_id = self._get_conversation_id(ctx)

        # Build payload
        payload = self._build_payload(ctx, user_message, conversation_id)

        auth_type = config["auth_type"]
        auth_config = config["auth_config"]

        # Track accumulated content for final message
        full_content = ""
        has_response = False

        try:
            async for event in client.call_webhook(
                payload=payload,
                auth_type=auth_type,
                auth_config=auth_config,
            ):
                event_type = event.get("type")

                if event_type == "item":
                    # Streaming chunk
                    content = event.get("content", "")
                    full_content += content
                    has_response = True

                    # Yield delta for each chunk
                    yield AgentRunResult.message_delta(ctx.run_id, MessageChunk(role="assistant", content=full_content))

                elif event_type == "end":
                    # Streaming completed
                    if full_content:
                        yield AgentRunResult.message_delta(
                            ctx.run_id, MessageChunk(role="assistant", content=full_content, is_final=True)
                        )

                elif event_type == "json":
                    # Non-streaming response
                    output_content = event.get("content", "")
                    if output_content:
                        has_response = True
                        yield AgentRunResult.message_delta(
                            ctx.run_id, MessageChunk(role="assistant", content=output_content, is_final=True)
                        )

        except N8nAPIError as e:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=e.message,
                code=e.code,
            )
            return
        except Exception as e:
            logger.exception(f"n8n runner unexpected error: {e}")
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"n8n runner error: {e}",
                code="n8n.unexpected_error",
            )
            return

        if not has_response:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error="n8n webhook returned no response",
                code="n8n.empty_response",
            )
            return

        # Store conversation_id in state for next run (scoped state)
        # Only update if we used a new conversation ID
        if conversation_id:
            yield AgentRunResult.state_updated(
                ctx.run_id,
                "external.conversation_id",
                conversation_id,
                scope="conversation",
            )

        yield AgentRunResult.run_completed(ctx.run_id)
