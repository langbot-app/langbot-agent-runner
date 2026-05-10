"""Configuration helpers for AgentRunner."""

from __future__ import annotations

import typing

from langbot_plugin.api.entities.builtin.agent_runner import AgentRunContext


def get_required_config(ctx: AgentRunContext, key: str) -> typing.Any:
    """Get a required configuration value.

    Raises KeyError if the key is not present in ctx.config.
    """
    if key not in ctx.config:
        raise KeyError(f"Required config key '{key}' not found in runner config")
    return ctx.config[key]


def get_optional_config(
    ctx: AgentRunContext, key: str, default: typing.Any = None
) -> typing.Any:
    """Get an optional configuration value with a default."""
    return ctx.config.get(key, default)
