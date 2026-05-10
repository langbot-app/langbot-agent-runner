"""Result helpers for AgentRunner."""

from __future__ import annotations

from langbot_plugin.api.entities.builtin.agent_runner import AgentRunResult
from langbot_plugin.api.entities.builtin.provider.message import Message, MessageChunk


def message_completed(text: str, role: str = "assistant") -> AgentRunResult:
    """Create a message.completed result with a text message."""
    message = Message(role=role, content=text)
    return AgentRunResult.message_completed(message)


def message_delta(
    text: str,
    role: str = "assistant",
    is_final: bool = False,
    sequence: int = 0,
) -> AgentRunResult:
    """Create a message.delta result with a text chunk."""
    chunk = MessageChunk(role=role, content=text, is_final=is_final, msg_sequence=sequence)
    return AgentRunResult.message_delta(chunk)


def run_failed(error: str, code: str | None = None, retryable: bool = False) -> AgentRunResult:
    """Create a run.failed result."""
    return AgentRunResult.run_failed(error=error, code=code or "runner.error", retryable=retryable)


def run_completed(
    text: str | None = None, role: str = "assistant", finish_reason: str = "stop"
) -> AgentRunResult:
    """Create a run.completed result.

    If text is provided, includes a final message.
    """
    message = None
    if text is not None:
        message = Message(role=role, content=text)
    return AgentRunResult.run_completed(message=message, finish_reason=finish_reason)
