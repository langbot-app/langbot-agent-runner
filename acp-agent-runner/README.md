# ACP Agent Runner

`acp-agent-runner` runs an Agent Client Protocol compatible agent process as a LangBot AgentRunner.

It is a thin runtime adapter:

- LangBot remains the control plane.
- The plugin starts an ACP server over stdio, such as `codex-acp`, `claude-agent-acp`, `opencode acp`, or `gemini --acp`.
- The plugin speaks ACP JSON-RPC: `initialize`, `session/new` or `session/resume`, and `session/prompt`.
- ACP `session/update` text chunks are streamed back to LangBot.
- LangBot tools, knowledge bases, and history are exposed through the SDK-owned run-scoped MCP bridge.

## Configuration

Common local examples:

```text
provider = claude-code
location = local
workspace = /path/to/workspace
```

```text
provider = codex
location = local
workspace = /path/to/workspace
```

For custom ACP commands, set `provider=custom` and `acp-command`, for example:

```text
provider = custom
location = local
workspace = /path/to/workspace
acp-command = opencode acp
```

Useful options:

- `provider`: `claude-code`, `codex`, `opencode`, `gemini`, or `custom`.
- `location`: `local` or `remote-ssh`.
- `workspace`: agent workspace directory. In `remote-ssh` mode this path is on the remote machine.
- `ssh-target`: SSH target for `remote-ssh`, for example `yhh@101.34.71.12`.
- `ssh-port`: SSH port. Defaults to `22`.
- `ssh-identity-file`: optional private key path on the LangBot host.
- `acp-command`: optional command override. Required only for `provider=custom`.
- `env-json`: JSON object merged into the process environment.
- `reuse-session`: persists and reuses `external.acp_session_id` when the ACP agent supports `session/resume` or `session/load`.
- `langbot-assets-enabled`: injects the LangBot run-scoped MCP bridge into ACP `mcpServers`.

Headless ACP permission requests are answered with `allow_once` by default.
LangBot does not yet expose an interactive approval UI for these requests.

For a remote Claude ACP process over SSH:

```text
provider = claude-code
location = remote-ssh
ssh-target = yhh@101.34.71.12
workspace = /home/yhh/langbot-e2e/acp-workspace
```

The plugin automatically starts the run-scoped MCP bridge, adds an SSH reverse
forward on the same SSH connection, and injects the remote-local HTTP MCP URL
into ACP `mcpServers`.

Remote hosts must allow non-interactive SSH login from the LangBot host and
must already have the selected agent runtime installed and logged in.
The SSH wrapper defaults to `bash`; Windows PowerShell remotes can still be
configured by setting the hidden `remote-shell=powershell` key manually until a
dedicated Windows UI option is added.

## Run-Scoped LangBot Assets

The plugin starts a temporary run-scoped MCP bridge for each run. The bridge URL and token are injected into ACP `mcpServers`, and the bridge is stopped when the run finishes.

The current LangBot `run_id` is included in the prompt for diagnostics only. ACP agents should follow MCP tool schemas exactly and should not add `run_id` to tool calls unless a specific tool schema asks for it.

## Scope

This plugin intentionally does not implement an agent platform, task board, workspace manager, or provider-specific CLI behavior. Provider setup, login, model selection, and tool permission semantics still belong to the ACP agent executable being launched.
