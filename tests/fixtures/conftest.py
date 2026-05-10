"""Shared pytest fixtures for contract tests."""

from __future__ import annotations

import pathlib

import pytest
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentInput,
    AgentResources,
    AgentRunContext,
    AgentRuntimeContext,
    AgentTrigger,
)

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent


@pytest.fixture
def minimal_context() -> AgentRunContext:
    """Create a minimal AgentRunContext for testing."""
    return AgentRunContext(
        run_id="test-run-001",
        trigger=AgentTrigger(type="message.received", source="pipeline"),
        input=AgentInput(text="Hello"),
        resources=AgentResources(),
        runtime=AgentRuntimeContext(sdk_protocol_version="1"),
        config={},
    )


@pytest.fixture
def context_with_config() -> AgentRunContext:
    """Create an AgentRunContext with config."""
    return AgentRunContext(
        run_id="test-run-002",
        trigger=AgentTrigger(type="message.received", source="pipeline"),
        input=AgentInput(text="Hello"),
        resources=AgentResources(),
        runtime=AgentRuntimeContext(sdk_protocol_version="1"),
        config={
            "base-url": "https://api.example.com/v1",
            "api-key": "test-key",
        },
    )
