"""DashScope Agent default runner implementation."""

from __future__ import annotations

from langbot_plugin.api.definition.components.agent_runner.runner import AgentRunner
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentRunContext,
    AgentRunnerCapabilities,
    AgentRunResult,
)
from langbot_plugin.api.entities.builtin.provider.message import Message


class DefaultAgentRunner(AgentRunner):
    """Default AgentRunner for DashScope Agent.

    Stub implementation for Phase 0. Returns a simple response.
    Full implementation in Phase 3.
    """

    @classmethod
    def get_capabilities(cls) -> AgentRunnerCapabilities:
        """Get runner capabilities."""
        return AgentRunnerCapabilities(
            streaming=True,
            stateful_session=True,
        )

    async def run(self, ctx: AgentRunContext) -> AgentRunResult:
        """Run the agent.

        Stub implementation: echoes app ID.
        """
        app_id = ctx.config.get("app-id", "unknown")
        app_type = ctx.config.get("app-type", "agent")
        text = ctx.input.to_text()
        message = Message(
            role="assistant",
            content=f"[stub] DashScope {app_type} {app_id}: {text}",
        )
        yield AgentRunResult.message_completed(message)
        yield AgentRunResult.run_completed()
