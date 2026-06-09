# LangBot Official AgentRunner Plugins

This repository contains official external-service AgentRunner plugins for
LangBot. Each plugin adapts a third-party agent, workflow, or app platform to
LangBot AgentRunner Protocol v1.

These plugins are protocol consumers. LangBot Host owns the run envelope,
resource authorization, fact stores, pull APIs, result normalization, and
delivery lifecycle. The plugins implement service-specific request/response
mapping and state handoff.

## Repository Scope

This repository does not implement LangBot EventGateway, event subscription,
event notification, scheduler, or event fanout. Those systems belong to LangBot
Host or separate event-focused branches.

The plugins here consume the `AgentRunContext` delivered by LangBot:

- `ctx.event`: event-first trigger metadata.
- `ctx.conversation`, `ctx.actor`, `ctx.subject`: current run scope metadata.
- `ctx.input`: current user/event input, including multimodal contents and
  artifact/file references.
- `ctx.context`: context access handles, cursors, inline policy, and available
  pull APIs.
- `ctx.resources`: run-scoped authorized models, tools, knowledge bases, files,
  and storage capabilities.
- `ctx.state`: small Host-projected state for the current run.
- `ctx.runtime`: deadline, trace id, protocol version, query id from migration
  adapter paths, and host runtime metadata.
- `ctx.delivery`: current delivery surface and streaming/edit capabilities.
- `ctx.config`: runner binding config.
- `ctx.adapter`: migration adapter fields; not part of Protocol v1 core and not
  a place for prompt, history, RAG results, tool schemas, or authorized
  resources.

LangBot does not inline full history by default. If a runner needs more context,
it should use the authorized pull APIs, such as history, event, artifact, state,
or storage APIs.

## External Harness Access To LangBot Assets

In-process runners, such as `local-agent`, should call `AgentRunAPIProxy`
directly.

Out-of-process harness runners, such as Claude Code and Codex, can optionally
enable the SDK-owned LangBot MCP bridge. The bridge is created per run from the
`AgentRunner` base class, exposes the run-authorized subset of the annotated
`AgentRunExternalTools` surface, delegates all LangBot asset access back through
`AgentRunAPIProxy`, and is stopped when the runner subprocess exits.

This is not a global LangBot MCP server and runner plugins do not hand-maintain
LangBot tool schemas. The SDK owns the annotations, schema generation, stdio MCP
proxy, and MCP config merge helper; individual runners only decide whether to
enable the bridge and how to pass the generated MCP config to their harness.
If a harness expects a bridge tool such as `langbot_history_page`, the runner
manifest must request the matching AgentRunner permission and LangBot Host must
grant it for that run.

## Remote Agent Daemon

Code-harness runners can share the dependency-free `remote_agent_daemon`
package for remote execution. Install this repository on the remote machine;
do not copy daemon source files by hand:

```bash
python -m venv .venv
. .venv/bin/activate
pip install "git+https://github.com/langbot-app/langbot-agent-runner.git@main"
```

Then start the daemon and select an adapter with `--agent`, for example:

```bash
python -m remote_agent_daemon \
  --agent codex \
  --host 0.0.0.0 \
  --port 8766 \
  --base-dir /path/to/langbot-remote-workspaces \
  --command-path /home/agent-user/.local/bin \
  --token "$LANGBOT_REMOTE_AGENT_TOKEN"
```

The daemon owns HTTP auth, workspace materialization, subprocess execution, and
result transport. Agent-specific behavior lives in small command adapters such
as `claude-code` and `codex`; future harnesses such as pi or kimi should add an
adapter instead of copying the daemon.

## Plugins

| Plugin | Runner ID | Replaces | Description |
| --- | --- | --- | --- |
| `dify-agent` | `plugin:langbot/dify-agent/default` | `dify-service-api` | Dify application integration |
| `n8n-agent` | `plugin:langbot/n8n-agent/default` | `n8n-service-api` | n8n workflow webhook integration |
| `coze-agent` | `plugin:langbot/coze-agent/default` | `coze-api` | Coze (扣子) bot integration |
| `claude-code-agent` | `plugin:langbot/claude-code-agent/default` | - | Local Claude Code CLI integration |
| `codex-agent` | `plugin:langbot/codex-agent/default` | - | Local Codex CLI integration |
| `dashscope-agent` | `plugin:langbot/dashscope-agent/default` | `dashscope-app-api` | Aliyun DashScope (百炼) integration |
| `langflow-agent` | `plugin:langbot/langflow-agent/default` | `langflow-api` | Langflow flow integration |
| `tbox-agent` | `plugin:langbot/tbox-agent/default` | `tbox-app-api` | Ant Tbox (百宝箱) integration |

The official `local-agent` runner is maintained in the sibling
`langbot-local-agent` repository because it directly calls LangBot-hosted
models/tools and has a separate test surface.

## Protocol v1 Alignment

External-service runners usually map LangBot input to a remote platform call:

- Read text and multimodal inputs from `ctx.input`.
- Read event and actor/subject metadata from `ctx.event`, `ctx.actor`, and
  `ctx.subject` when the target platform needs it.
- Read delivery/runtime decisions from `ctx.delivery` and `ctx.runtime`.
- Read static runner binding config from `ctx.config`.
- Respect `ctx.resources` and use `AgentRunAPIProxy` for any Host-mediated
  model, tool, knowledge, history, event, artifact, state, or storage access.
- Use `ctx.context` to decide whether more history/artifact/state can be pulled.

Pipeline adapter fields are adapter-only:

- Business parameters may appear in `ctx.adapter.extra.params`.
- Prompt data must not be read from `ctx.adapter.extra.prompt`.
- Static binding prompt belongs to `ctx.config.prompt` when the runner has such
  config. Effective prompt/instruction data, if needed, should be pulled through
  the Host prompt API when `ctx.context.available_apis.prompt_get` is available.
- History is not delivered through `ctx.bootstrap`; use authorized history pull
  APIs when more context is needed.

Do not depend on top-level `ctx.params`, `ctx.prompt`, `ctx.messages`, or
`ctx.bootstrap` as Protocol v1 fields. New runner code should prefer
event-first fields and pull APIs.

Third-party agent platforms usually have their own prompt, app, bot, or workflow
configuration. These runners should not reinterpret a LangBot prompt as the
third-party platform prompt unless the specific platform integration explicitly
requires that mapping.

## State And History

External conversation IDs, session IDs, workflow run IDs, and checkpoints should
be stored in plugin storage or the Host state API. Runners must not rely on
LangBot internal conversation UUID structures as private implementation details.

Recommended patterns:

- Store external session identifiers with Host state APIs such as
  `AgentRunAPIProxy.state_set(...)`, or with plugin storage.
- Page or search transcript history only when needed.
- Keep large payloads as artifacts and read them through artifact APIs.
- Return `state.updated` when the runner wants LangBot to persist small JSON
  state.
- Return `artifact.created` for generated files or large outputs.

## Development

### Requirements

- Python 3.10+
- `uv` package manager

### Setup

```bash
uv sync --dev
```

### Run Tests

```bash
uv run pytest
```

### Run Lint

```bash
uv run ruff check .
```

## Architecture

- Each plugin is a root-level directory, not `packages/<plugin>`.
- This repository is distributed as a plugin collection, not as an importable
  `langbot_agent_runner` Python package.
- Each plugin declares one or more AgentRunner components.
- All runners use AgentRunner Protocol v1.
- Host authorization is run-scoped and enforced through `run_id`,
  `ctx.resources`, and caller plugin identity.
- Pipeline adapter conversion is handled by LangBot Host before the runner is
  invoked; this repository does not own Pipeline internals.

## Related

- [LangBot Plugin SDK](https://github.com/langbot-app/langbot-plugin-sdk) -
  Plugin development SDK and runtime.
- [LangBot](https://github.com/langbot-app/LangBot) - Main LangBot application
  and Host implementation.
- AgentRunner Protocol v1 - see
  `LangBot/docs/agent-runner-pluginization/PROTOCOL_V1.md` in the LangBot
  repository.
