"""Langflow Agent default runner implementation."""

from __future__ import annotations

from langbot_plugin.api.definition.components.agent_runner.runner import AgentRunner
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentRunContext,
    AgentRunnerCapabilities,
    AgentRunResult,
)
from langbot_plugin.api.entities.builtin.provider.message import Message


class DefaultAgentRunner(AgentRunner):
    """Default AgentRunner for Langflow Agent.

    Stub implementation for Phase 0. Returns a simple response.
    Full implementation in Phase 2.
    """

    @classmethod
    def get_capabilities(cls) -> AgentRunnerCapabilities:
        """Get runner capabilities."""
        return AgentRunnerCapabilities(
            stateful_session=True,
        )

    async def run(self, ctx: AgentRunContext) -> AgentRunResult:
        """Run the agent.

        Stub implementation: echoes flow ID.
        """
        flow_id = ctx.config.get("flow-id", "unknown")
        base_url = ctx.config.get("base-url", "http://localhost:7860")
        text = ctx.input.to_text()
        message = Message(
            role="assistant",
            content=f"[stub] Langflow flow {flow_id} at {base_url}: {text}",
        )
        yield AgentRunResult.message_completed(message)
        yield AgentRunResult.run_completed()
