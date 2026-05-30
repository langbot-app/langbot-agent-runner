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

| Field | Default | Description |
| --- | --- | --- |
| `cli-command` | `claude` | CLI command to execute. May include fixed command tokens. |
| `extra-args` | empty | Extra CLI arguments appended after runner-owned arguments. |
| `working-directory` | empty | Directory used as Claude Code's project cwd. If empty, the runner reuses stored `external.working_directory` or falls back to its current process cwd. |
| `inject-context` | `true` | Write LangBot run context files and prepend their paths to the current Claude Code prompt. |
| `context-directory` | `.langbot/agent-runner` | Directory, relative to `working-directory` unless absolute, where per-run context and generated MCP config files are written. |
| `enable-langbot-mcp` | `false` | Start the SDK-owned per-run LangBot MCP bridge and merge it into the generated Claude Code MCP config. |
| `inject-skills` | `true` | Write configured skills into Claude Code's native `.claude/skills/<name>/SKILL.md` layout. |
| `skills-json` | empty | Optional JSON array, or `{ "skills": [...] }`, with entries like `{ "name": "support-playbook", "content": "..." }`. |
| `mcp-config-json` | empty | Optional Claude Code MCP config JSON object to write for this run and pass with `--mcp-config`. |
| `mcp-config-file` | empty | Existing MCP config file path to pass with `--mcp-config`; relative paths resolve from `working-directory`. |
| `strict-mcp-config` | `true` | Add `--strict-mcp-config` when a generated or configured MCP config is used. |
| `model` | empty | Optional Claude Code model argument. |
| `output-format` | `json` | `json`, `stream-json`, or `text`. `json` and `stream-json` allow session id capture. |
| `input-format` | `text` | `text` or `stream-json`. |
| `setting-sources` | empty | Optional Claude Code `--setting-sources` value, for example `local`. |
| `permission-mode` | `plan` | Claude Code permission mode. The default is intentionally non-mutating for the minimal IM runner path. |
| `tools` | empty | Optional `--tools` value. The manifest default disables tools for the minimal runner; set `default` or a tool list when a binding should grant Claude Code tool access. |
| `allowed-tools` | empty | Optional space-separated `--allowedTools` values. |
| `disallowed-tools` | `AskUserQuestion` | Optional space-separated `--disallowedTools` values. The default avoids non-interactive clarification calls. |
| `max-turns` | `1` | Maximum Claude Code turns for one LangBot run. Increase this in trusted code-agent bindings. |
| `verbose` | `false` | Add `--verbose`. The runner always adds it for `output-format=stream-json` because current Claude Code requires it. |
| `resume` | `true` | Add `--resume <session_id>` when a prior session id exists in runner state. |
| `timeout` | `300` | Process timeout in seconds. |
| `dry-run` | `false` | Return a mock response without invoking the CLI. |
| `mock-response` | empty | Optional dry-run response body. |

Dry-run mode can also be enabled with `LANGBOT_CLAUDE_CODE_AGENT_DRY_RUN=1` or
`CLAUDE_CODE_AGENT_DRY_RUN=1`.

## Notes

The plugin itself is stateless. When Claude Code emits a `session_id`, the
runner asks LangBot to store it in conversation-scoped runner state under
`external.session_id`; it also stores `external.working_directory` because
Claude Code resume lookup is project/cwd scoped. Later runs pass the session id
back with `--resume` and use the configured or stored working directory.

LangBot still delivers the full Protocol v1 run context, including trigger,
event, actor, subject, delivery, and state fields. This minimal runner currently
passes that event/resource/state summary to Claude Code as read-only context
files. LangBot-owned skills and external MCP resources can still be projected
through `skills-json` and `mcp-config-json`.

When `enable-langbot-mcp=true`, the runner calls the SDK base helper to create a
per-run LangBot MCP bridge. That bridge exposes only the tools authorized for
the current run from the SDK-owned annotated `AgentRunExternalTools` surface and
delegates all LangBot asset access through `AgentRunAPIProxy`; this runner only
merges the generated MCP server config into Claude Code's MCP config and adds
the LangBot MCP tool pattern to `--allowedTools`.

This plugin intentionally does not implement sandboxing, workspace mounting, or
tool policy management. In real mode it requires a working local Claude Code CLI
on the LangBot runtime host.
