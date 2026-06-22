# Codex AgentRunner

Runner ID: `plugin:langbot/codex-agent/default`

This plugin runs Codex CLI through the app-server JSON-RPC protocol and injects the run-scoped LangBot MCP assets supplied by the SDK.

## Runtime

- Local: starts `codex app-server --listen stdio://` and speaks JSON-RPC over stdio.
- SSH: starts the same app-server command on a remote machine and uses the SDK reverse tunnel helper for LangBot MCP assets.
- Daemon: a user-side daemon connects outward to LangBot and starts Codex app-server on the user machine.

Codex CLI must already be installed and authenticated where the command runs. The runner prepares an isolated per-run `CODEX_HOME` under the workspace, links the user's Codex auth/session state, and writes managed MCP server config to `config.toml` instead of passing MCP secrets on argv.

## Steering (follow-up input)

This runner declares `capabilities.steering: true`. When a run is still in
progress and the user sends another message, LangBot absorbs it into the active
run instead of starting a new one. The runner drains these follow-ups at each
turn boundary via `steering_pull` and runs them as additional turns that resume
the same Codex thread (`thread/resume`). The run emits a single terminal
`run.completed` once no follow-ups remain.

Notes:

- Follow-ups are injected between turns, not mid-token.
- Follow-up turns currently carry text only; attachments on follow-ups are not
  yet forwarded.
- Steering only applies when the run has a conversation scope; otherwise the
  runner transparently falls back to single-turn execution.
