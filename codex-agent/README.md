# Codex Agent

Minimal LangBot AgentRunner plugin for the Codex CLI.

Runner ID:

```text
plugin:langbot/codex-agent/default
```

The runner reads the current input text from `ctx.input.to_text()`, invokes
`codex exec` non-interactively, and returns the final Codex message as an
assistant message.

## Configuration

The pipeline binding form intentionally exposes only product-level settings:

| Field | Default | Description |
| --- | --- | --- |
| `execution-mode` | `local` | `local` runs Codex on the LangBot runtime host. `remote` sends the run to a remote daemon. |
| `remote-endpoint` | empty | Remote daemon base URL, for example `http://127.0.0.1:8766`. Required when `execution-mode=remote`. |
| `remote-token` | empty | Optional bearer token sent to the remote daemon. |
| `working-directory` | empty | Directory used as Codex's project cwd. If empty, the runner reuses stored `external.working_directory` or falls back to its current process cwd. |
| `model` | empty | Optional Codex `--model` argument. |
| `timeout` | `300` | Process timeout in seconds. |

Code-level tests and wrappers may still pass a few runner-owned compatibility
keys, including `cli-command`, `extra-args`, `inject-context`,
`context-directory`, `approval-policy`, `sandbox`, `output-format`,
`skip-git-repo-check`, `ephemeral`, `ignore-rules`, `config-overrides`,
`environment-json`, and `resume`. The pipeline form intentionally does not ask
users to write Codex profiles, MCP JSON, skills JSON, dry-run settings, or mock
responses. The local subprocess starts from a small allowlisted environment
surface for CLI auth, proxy, locale, and certificate settings. `environment-json`
can add runtime-local non-secret settings such as proxies, but it cannot
override protected variables such as `HOME`, `PATH`, `CODEX_HOME`, `PYTHONPATH`,
or `LANGBOT_*`.

## Remote Daemon MVP

The first remote execution path is intentionally narrow:

- The runner sends one HTTP JSON request to the daemon and waits for completion.
- The daemon materializes runner-projected context files under a daemon-owned
  workspace directory.
- The daemon executes Codex locally and returns stdout/stderr/returncode.

Start the shared daemon on the remote machine:

```bash
python -m remote_agent_daemon \
  --agent codex \
  --host 0.0.0.0 \
  --port 8766 \
  --base-dir /path/to/langbot-remote-workspaces \
  --command-path /home/codex-user/.npm-global/bin \
  --token "$LANGBOT_REMOTE_AGENT_TOKEN"
```

The legacy `cd codex-agent && python -m pkg.remote_daemon ...` entry point is
still available and forces the Codex adapter.

Then configure the runner binding with:

```text
execution-mode=remote
remote-endpoint=http://<daemon-host>:8766
remote-token=<same token, if configured>
```

In remote mode, `cli-command` is resolved on the daemon host, not on the LangBot
runtime host. Start the daemon with
`--command-path` / `LANGBOT_REMOTE_AGENT_COMMAND_PATH` when `codex` only exists
in a user-local bin directory that a service shell would not normally load.

Remote mode stores `external.session_id`, `external.runtime_id`, and
`external.workspace_key` in Host state. It does not persist the daemon's absolute
working directory as resume state, because that path is only meaningful on the
daemon machine.

## Notes

The plugin itself is stateless. When Codex emits a `thread_id`, the runner asks
LangBot to store it in conversation-scoped runner state under
`external.session_id`; it also stores `external.working_directory` in local mode
because Codex resume lookup is project/cwd scoped. Later local runs use
`codex exec resume` with the stored thread id.

LangBot still delivers the full Protocol v1 run context, including trigger,
event, actor, subject, delivery, and state fields. This runner passes that
event/resource/state summary to Codex as read-only context files.

In local mode, the runner automatically creates a per-run LangBot MCP bridge
when the LangBot runtime is bound. That bridge exposes only the tools and
knowledge access authorized for the current run from the SDK-owned
`AgentRunExternalTools` surface and delegates all LangBot asset access through
`AgentRunAPIProxy`. The runner writes the generated MCP config into a per-run
`CODEX_HOME/config.toml` with `0600` permissions, and filters user
`config-overrides` that try to write `mcp_servers.*`. The per-run `CODEX_HOME`
inherits local Codex auth/session and non-MCP provider config from the runtime
user's shared Codex home, but strips global `mcp_servers` before appending the
LangBot-managed bridge. This keeps scoped MCP secrets out of Codex argv and
ordinary command logs without breaking the operator's existing Codex login.
LangBot bridge tools are marked with Codex `approval_mode="approve"` so
non-interactive runs can call the run-scoped bridge while LangBot still performs
resource authorization.

The runner writes Codex JSONL stdout to `codex-events.jsonl` in the run
directory, and writes non-empty stderr to `codex-stderr.log`. These files are
diagnostic artifacts for local validation and should be treated as run-scoped
data.

This plugin intentionally does not implement full workspace lifecycle,
publishing-grade sandboxing, or tool policy management. In real mode it requires
a working Codex CLI on the LangBot runtime host, or on the configured remote
daemon host when `execution-mode=remote`.
