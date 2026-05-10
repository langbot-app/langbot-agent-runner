"""HTTP helpers for AgentRunner."""

from __future__ import annotations

import aiohttp
from langbot_plugin.api.entities.builtin.agent_runner import AgentRunContext

from langbot_agent_runner_utils.config import get_optional_config


def http_timeout(ctx: AgentRunContext, default_seconds: int = 120) -> aiohttp.ClientTimeout:
    """Create an aiohttp ClientTimeout from runner config or default.

    Looks for 'timeout' in ctx.config, falls back to default_seconds.
    """
    timeout_seconds = get_optional_config(ctx, "timeout", default_seconds)
    return aiohttp.ClientTimeout(total=timeout_seconds)
