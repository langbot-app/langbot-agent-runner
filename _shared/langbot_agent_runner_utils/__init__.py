"""Development-time utilities for AgentRunner plugins.

NOTE: This module is for development only. Plugins should NOT import from
`_shared` at runtime, because individual plugin directories may be copied
or installed without the parent `_shared` directory.

For runtime helpers, copy the needed functions into each plugin's pkg/ directory.
"""

from langbot_agent_runner_utils.config import (
    get_optional_config,
    get_required_config,
)
from langbot_agent_runner_utils.context import (
    get_text_input,
    stable_conversation_id,
    stable_user_id,
)
from langbot_agent_runner_utils.http import http_timeout
from langbot_agent_runner_utils.result import (
    message_completed,
    message_delta,
    run_completed,
    run_failed,
)

__all__ = [
    "get_text_input",
    "get_required_config",
    "get_optional_config",
    "message_completed",
    "message_delta",
    "run_failed",
    "run_completed",
    "http_timeout",
    "stable_user_id",
    "stable_conversation_id",
]
