"""Tbox Agent default runner implementation."""

from __future__ import annotations

from langbot_plugin.api.definition.components.agent_runner.runner import AgentRunner
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentRunContext,
    AgentRunnerCapabilities,
    AgentRunResult,
)
from langbot_plugin.api.entities.builtin.provider.message import Message


class DefaultAgentRunner(AgentRunner):
    """Default AgentRunner for Tbox Agent.

    Stub implementation for Phase 0. Returns a simple response.
    Full implementation in Phase 3.
    """

    @classmethod
    def get_capabilities(cls) -> AgentRunnerCapabilities:
        """Get runner capabilities."""
        return AgentRunnerCapabilities(
            stateful_session=True,
        )

    async def run(self, ctx: AgentRunContext) -> AgentRunResult:
        """Run the agent.

        Stub implementation: echoes app ID.
        """
        app_id = ctx.config.get("app-id", "unknown")
        text = ctx.input.to_text()
        message = Message(
            role="assistant",
            content=f"[stub] Tbox app {app_id}: {text}",
        )
        yield AgentRunResult.message_completed(message)
        yield AgentRunResult.run_completed()
