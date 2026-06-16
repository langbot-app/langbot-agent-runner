# Claude Code AgentRunner

Runner ID: `plugin:langbot/claude-code-agent/default`

This plugin runs Claude Code CLI in non-interactive mode and injects the run-scoped LangBot MCP assets supplied by the SDK.

## Runtime

- Local: starts `claude -p --verbose --output-format stream-json`.
- SSH: starts the same command on a remote machine and uses the SDK reverse tunnel helper for LangBot MCP assets.
- Daemon: a user-side daemon connects outward to LangBot and starts Claude Code on the user machine.

Claude Code must already be installed and authenticated where the command runs. Set `command` when the binary is not on `PATH`.
