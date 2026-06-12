"""ACP Agent Runner plugin entry point."""

from __future__ import annotations

from langbot_plugin.api.definition.plugin import BasePlugin


class AcpAgentRunnerPlugin(BasePlugin):
    """Agent Client Protocol runner plugin."""

    def __init__(self):
        super().__init__()

    async def initialize(self) -> None:
        """Initialize the plugin."""
        return
