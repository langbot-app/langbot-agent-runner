"""Compatibility entry point for the shared LangBot remote agent daemon."""

# ruff: noqa: E402

from __future__ import annotations

import pathlib
import sys
import typing

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from remote_agent_daemon.core import (  # noqa: E402
    CLAUDE_CODE_ADAPTER,
    RemoteAgentHTTPServer,
    build_claude_code_command,
    compatibility_main,
)
from remote_agent_daemon.core import (
    handle_run_request as _handle_run_request,
)

RemoteClaudeHTTPServer = RemoteAgentHTTPServer
_build_command = build_claude_code_command


async def handle_run_request(
    payload: dict[str, typing.Any],
    base_dir: pathlib.Path,
    command_path: str = "",
) -> dict[str, typing.Any]:
    return await _handle_run_request(
        payload,
        base_dir,
        command_path,
        forced_agent=CLAUDE_CODE_ADAPTER.name,
    )


def main(argv: list[str] | None = None) -> int:
    return compatibility_main(
        argv=argv,
        agent=CLAUDE_CODE_ADAPTER.name,
        env_prefix="LANGBOT_REMOTE_CLAUDE",
        default_port=8765,
        description="Run the LangBot Claude Code remote daemon.",
        label="LangBot Claude Code remote",
        command_help_name="Claude Code",
    )


if __name__ == "__main__":
    raise SystemExit(main())
