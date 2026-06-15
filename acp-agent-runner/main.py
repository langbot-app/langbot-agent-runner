"""ACP Agent Runner plugin entry point."""

from __future__ import annotations

from langbot_plugin.api.definition.plugin import BasePlugin
from pkg.daemon_hub import daemon_hub_config_from_plugin_config, get_daemon_hub


class AcpAgentRunnerPlugin(BasePlugin):
    """Agent Client Protocol runner plugin."""

    def __init__(self):
        super().__init__()

    async def initialize(self) -> None:
        """Initialize the plugin."""
        config = daemon_hub_config_from_plugin_config(self.get_config())
        if config["enabled"]:
            await get_daemon_hub().start(
                host=config["host"],
                port=config["port"],
                token=config["token"],
            )
