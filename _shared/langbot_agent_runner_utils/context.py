"""Context helpers for AgentRunner."""

from __future__ import annotations

from langbot_plugin.api.entities.builtin.agent_runner import AgentRunContext


def get_text_input(ctx: AgentRunContext) -> str:
    """Get text input from context.

    Uses ctx.input.to_text() for pure text representation.
    """
    return ctx.input.to_text()


def stable_user_id(ctx: AgentRunContext) -> str | None:
    """Get a stable user identifier from context.

    Returns sender_id from conversation context if available.
    """
    if ctx.conversation and ctx.conversation.sender_id:
        return ctx.conversation.sender_id
    return None


def stable_conversation_id(ctx: AgentRunContext) -> str | None:
    """Get a stable conversation identifier from context.

    Returns conversation_id from conversation context if available.
    """
    if ctx.conversation and ctx.conversation.conversation_id:
        return ctx.conversation.conversation_id
    return None
