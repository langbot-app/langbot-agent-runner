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
    CODEX_ADAPTER,
    RemoteAgentHTTPServer,
    build_codex_command,
    compatibility_main,
)
from remote_agent_daemon.core import (
    handle_run_request as _handle_run_request,
)

RemoteCodexHTTPServer = RemoteAgentHTTPServer
_build_command = build_codex_command


async def handle_run_request(
    payload: dict[str, typing.Any],
    base_dir: pathlib.Path,
    command_path: str = "",
) -> dict[str, typing.Any]:
    return await _handle_run_request(
        payload,
        base_dir,
        command_path,
        forced_agent=CODEX_ADAPTER.name,
    )


def main(argv: list[str] | None = None) -> int:
    return compatibility_main(
        argv=argv,
        agent=CODEX_ADAPTER.name,
        env_prefix="LANGBOT_REMOTE_CODEX",
        default_port=8766,
        description="Run the LangBot Codex remote daemon.",
        label="LangBot Codex remote",
        command_help_name="Codex",
    )


if __name__ == "__main__":
    raise SystemExit(main())
