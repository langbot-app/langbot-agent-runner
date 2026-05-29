# Codex Agent

Minimal LangBot AgentRunner plugin for the local Codex CLI.

Runner ID:

```text
plugin:langbot/codex-agent/default
```

The runner reads the current input text from `ctx.input.to_text()`, invokes
`codex exec` non-interactively, and returns the final Codex message as an
assistant message.

## Configuration

| Field | Default | Description |
| --- | --- | --- |
| `cli-command` | `codex` | CLI command to execute. Use `codex` unless a wrapper command is required. |
| `extra-args` | empty | Extra CLI arguments appended after runner-owned options and before the prompt marker. |
| `working-directory` | empty | Directory used as Codex's project cwd. If empty, the runner reuses stored `external.working_directory` or falls back to its current process cwd. |
| `inject-context` | `true` | Write LangBot run context files and prepend their paths to the Codex prompt. |
| `context-directory` | `.langbot/agent-runner` | Directory, relative to `working-directory` unless absolute, where per-run context files are written. |
| `inject-skills` | `true` | Write configured skills into the per-run `codex-skills/<name>/SKILL.md` directory and mention the directory in the prompt. |
| `skills-json` | empty | Optional JSON array, or `{ "skills": [...] }`, with entries like `{ "name": "support-playbook", "content": "..." }`. |
| `mcp-config-json` | empty | Optional MCP config JSON. The runner writes it to the run directory and best-effort maps `mcpServers` to Codex `--config mcp_servers.*` overrides. |
| `mcp-config-file` | empty | Existing MCP config file path to reference in the prompt; relative paths resolve from `working-directory`. |
| `model` | empty | Optional Codex `--model` argument. |
| `profile` | empty | Optional Codex `--profile` argument for new sessions. |
| `sandbox` | `read-only` | Codex sandbox mode for new sessions. |
| `output-format` | `json` | `json` uses Codex JSONL events and captures `thread_id`; `text` parses plain stdout or the last-message file. |
| `skip-git-repo-check` | `true` | Add `--skip-git-repo-check`, useful for LangBot workspaces that are not a single Git repository. |
| `ephemeral` | `false` | Add `--ephemeral`. |
| `ignore-rules` | `false` | Add `--ignore-rules`. |
| `config-overrides` | empty | JSON object/list or shell string of Codex `--config key=value` overrides. |
| `environment-json` | empty | Optional JSON object of environment variables for the Codex subprocess. Use this for runtime-local settings such as proxy variables; do not store secrets here. |
| `resume` | `true` | Use `codex exec resume <thread_id>` when a prior thread id exists in runner state. |
| `timeout` | `300` | Process timeout in seconds. |
| `dry-run` | `false` | Return a mock response without invoking the CLI. |
| `mock-response` | empty | Optional dry-run response body. |

Dry-run mode can also be enabled with `LANGBOT_CODEX_AGENT_DRY_RUN=1` or
`CODEX_AGENT_DRY_RUN=1`.

## Notes

The plugin itself is stateless. When Codex emits a `thread_id`, the runner asks
LangBot to store it in conversation-scoped runner state under
`external.session_id`; it also stores `external.working_directory` because Codex
resume lookup is project/cwd scoped. Later runs use `codex exec resume` with the
stored thread id.

LangBot still delivers the full Protocol v1 run context, including trigger,
event, actor, subject, delivery, and state fields. This minimal runner passes
that event/resource/state summary to Codex as read-only context files. LangBot
owned skills and MCP resources should be converted by Host or binding
configuration into `skills-json`, `mcp-config-json`, or Codex config overrides;
this runner only adapts those scoped resources into Codex's native harness
shape.

The runner writes Codex JSONL stdout to `codex-events.jsonl` in the run
directory, and writes non-empty stderr to `codex-stderr.log`. These files are
diagnostic artifacts for local validation and should be treated as run-scoped
data.

This plugin intentionally does not implement full workspace lifecycle,
publishing-grade sandboxing, or tool policy management. In real mode it requires
a working local Codex CLI on the LangBot runtime host.
