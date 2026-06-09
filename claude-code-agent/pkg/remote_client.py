"""Remote daemon client helpers for Claude Code runner execution."""

from __future__ import annotations

import json
import pathlib
import sys
import typing
import urllib.error
import urllib.request

from langbot_plugin.api.entities.builtin.agent_runner import AgentRunContext

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from remote_agent_daemon import channel  # noqa: E402


def remote_workspace_key(ctx: AgentRunContext) -> str:
    stored = str(ctx.state.conversation.get("external.workspace_key") or "").strip()
    if stored:
        return stored

    conversation = ctx.conversation
    parts = []
    for value in (
        getattr(conversation, "workspace_id", None),
        getattr(conversation, "bot_id", None),
        getattr(conversation, "conversation_id", None),
        getattr(conversation, "thread_id", None),
    ):
        if value:
            parts.append(str(value))
    return ":".join(parts) or str(ctx.state.conversation.get("external.conversation_id") or "default")


def post_remote_run(
    endpoint: str,
    token: str,
    request_payload: dict[str, typing.Any],
    timeout: float,
) -> dict[str, typing.Any]:
    data = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/run",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"ok": False, "code": "connection_error", "error": str(e), "retryable": True}

    try:
        parsed = json.loads(body) if body else {}
    except json.JSONDecodeError as e:
        return {"ok": False, "code": "invalid_response", "error": f"invalid remote response: {e}"}
    if not isinstance(parsed, dict):
        return {"ok": False, "code": "invalid_response", "error": "remote response must be an object"}
    return parsed


async def run_remote_channel(
    endpoint: str,
    token: str,
    request_payload: dict[str, typing.Any],
    timeout: float,
    *,
    mcp_handler: channel.MCPHandler | None = None,
) -> dict[str, typing.Any]:
    return await channel.run_remote_channel(
        endpoint,
        token,
        request_payload,
        timeout,
        mcp_handler=mcp_handler,
    )
