"""LiteLLM Agent Platform plugin entry point."""

from __future__ import annotations

import logging

from langbot_plugin.api.definition.plugin import BasePlugin
from pkg.langbot_mcp_gateway import LangBotMCPGateway

logger = logging.getLogger(__name__)


def _to_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


class LiteLLMAgentPlatformPlugin(BasePlugin):
    """LiteLLM Agent Platform plugin."""

    def __init__(self):
        super().__init__()
        self._mcp_gateway: LangBotMCPGateway | None = None

    async def initialize(self) -> None:
        """Initialize the plugin."""
        config = self.get_config()
        if not _to_bool(config.get("mcp-gateway-enabled"), False):
            return

        token = str(config.get("mcp-gateway-token") or "").strip()
        if not token:
            raise RuntimeError("mcp-gateway-token is required when mcp-gateway-enabled is true")

        try:
            port = int(config.get("mcp-gateway-port") or 8765)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("mcp-gateway-port must be an integer") from exc

        try:
            timeout = float(config.get("mcp-gateway-timeout") or 60)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("mcp-gateway-timeout must be a number") from exc

        gateway = LangBotMCPGateway(
            self,
            host=str(config.get("mcp-gateway-host") or "127.0.0.1").strip(),
            port=port,
            token=token,
            request_timeout=timeout,
        )
        gateway.start()
        self._mcp_gateway = gateway
        public_url = str(config.get("mcp-gateway-public-url") or "").strip() or gateway.endpoint
        logger.info("LiteLLM Agent Platform LangBot MCP gateway available at %s", public_url)

    def __del__(self) -> None:
        gateway = getattr(self, "_mcp_gateway", None)
        if gateway is not None:
            gateway.stop()
