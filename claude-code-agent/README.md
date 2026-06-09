# Claude Code Agent

Minimal LangBot AgentRunner plugin for the local Claude Code CLI.

Runner ID:

```text
plugin:langbot/claude-code-agent/default
```

The runner reads the current input text from `ctx.input.to_text()`, invokes
Claude Code in print mode, and returns the final CLI result as an assistant
message.

## Configuration

The pipeline binding form intentionally exposes only product-level settings:

| Field | Default | Description |
| --- | --- | --- |
| `execution-mode` | `local` | `local` runs Claude Code on the LangBot runtime host. `remote` sends the run to a remote daemon. |
| `remote-endpoint` | empty | Remote daemon base URL, for example `http://127.0.0.1:8765`. Required when `execution-mode=remote`. |
| `remote-token` | empty | Optional bearer token sent to the remote daemon. |
| `working-directory` | empty | Directory used as Claude Code's project cwd. If empty, the runner reuses stored `external.working_directory` or falls back to its current process cwd. |
| `model` | empty | Optional Claude Code model argument. |
| `dangerously-skip-permissions` | `false` | Explicitly add Claude Code `--dangerously-skip-permissions`. Keep this disabled unless the binding runs in an operator-owned trusted runtime. |
| `timeout` | `300` | Process timeout in seconds. |

Real runs do not add `--dangerously-skip-permissions` by default. That bypass is
an explicit high-risk binding option for trusted self-host or containerized
runtimes. Local and remote subprocesses receive a small allowlisted environment
surface for CLI auth, proxy, locale, and certificate settings; LangBot runtime
variables are not inherited wholesale. Protect remote daemons with a bearer
token, a private network, or an SSH tunnel.

## Remote Daemon

Remote execution uses the shared LangBot remote-agent daemon and a run-scoped
bidirectional channel:

- The runner opens a channel to the daemon and sends one semantic run request.
- The daemon materializes LangBot run context files under a daemon-owned
  workspace directory.
- The daemon writes a local `langbot_agent` MCP config for Claude Code.
- Claude Code talks to that local MCP shim over stdio; the shim forwards MCP
  tool calls through the active run channel back to the runner.
- The runner delegates those tool calls to LangBot through the SDK-owned
  `AgentRunMCPBridge`, so history, knowledge retrieval, tools, and skill
  activation stay run-scoped and authorized by Host state.
- The daemon executes Claude Code locally and returns stdout/stderr/returncode.

Start the shared daemon on the remote machine:

```bash
python -m remote_agent_daemon \
  --agent claude-code \
  --host 0.0.0.0 \
  --port 8765 \
  --base-dir /path/to/langbot-remote-workspaces \
  --command-path /home/claude-user/.npm-global/bin \
  --token "$LANGBOT_REMOTE_AGENT_TOKEN"
```

The legacy `cd claude-code-agent && python -m pkg.remote_daemon ...` entry point
is still available and forces the Claude Code adapter.

Then configure the runner binding with:

```text
execution-mode=remote
remote-endpoint=http://<daemon-host>:8765
remote-token=<same token, if configured>
```

In remote mode, `cli-command` is resolved on the daemon host, not on the LangBot
runtime host. Start the daemon with
`--command-path` / `LANGBOT_REMOTE_AGENT_COMMAND_PATH` when `claude` only exists
in a user-local bin directory that a service shell would not normally load.

Remote mode does not ask users to hand-write MCP or skills JSON. LangBot tools
and skills that are visible to the current pipeline are exposed automatically
through the run-scoped MCP bridge.

Remote mode stores `external.session_id`, `external.runtime_id`, and
`external.workspace_key` in Host state. It does not persist the daemon's absolute
working directory as resume state, because that path is only meaningful on the
daemon machine.

## Notes

The plugin itself is stateless. When Claude Code emits a `session_id`, the
runner asks LangBot to store it in conversation-scoped runner state under
`external.session_id`; it also stores `external.working_directory` because
Claude Code resume lookup is project/cwd scoped. Later runs pass the session id
back with `--resume` and use the configured or stored working directory.

LangBot still delivers the full Protocol v1 run context, including trigger,
event, actor, subject, delivery, and state fields. This minimal runner currently
passes that event/resource/state summary to Claude Code as read-only context
files.

This plugin intentionally does not implement sandboxing, workspace mounting, or
tool policy management for the external CLI itself. In real mode it requires a
working local Claude Code CLI on the LangBot runtime host, or on the configured
remote daemon host when `execution-mode=remote`.
