# Claude Code AgentRunner

Runner ID: `plugin:langbot/claude-code-agent/default`

This plugin runs Claude Code CLI in non-interactive mode and injects the run-scoped LangBot MCP assets supplied by the SDK.

## Runtime

- Local: starts `claude -p --verbose --output-format stream-json`.
- SSH: starts the same command on a remote machine and uses the SDK reverse tunnel helper for LangBot MCP assets.
- Daemon: a user-side daemon connects outward to LangBot and starts Claude Code on the user machine.

Claude Code must already be installed and authenticated where the command runs. Set `command` when the binary is not on `PATH`.

## Steering (follow-up input)

This runner declares `capabilities.steering: true`. When a run is still in
progress and the user sends another message, LangBot absorbs it into the active
run instead of starting a new one. The runner drains these follow-ups at each
turn boundary via `steering_pull` and runs them as additional turns that resume
the same Claude Code session with `claude --resume <session-id>` (a new session
is created once with `--session-id`). The run emits a single terminal
`run.completed` once no follow-ups remain.

Notes:

- Follow-ups are injected between turns, not mid-token.
- Follow-up turns currently carry text only; attachments on follow-ups are not
  yet forwarded.
- Steering only applies when the run has a conversation scope; otherwise the
  runner transparently falls back to single-turn execution.
