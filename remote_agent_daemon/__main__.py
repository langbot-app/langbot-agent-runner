"""Module entry point for the shared LangBot remote agent daemon."""

from __future__ import annotations

from remote_agent_daemon.core import main

if __name__ == "__main__":
    raise SystemExit(main())
