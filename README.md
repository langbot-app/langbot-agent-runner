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
- `ctx.input`: current user/event input, including multimodal contents and
  artifact/file references.
- `ctx.context`: context access handles, cursors, inline policy, and available
  pull APIs.
- `ctx.resources`: run-scoped authorized models, tools, knowledge bases, files,
  and storage capabilities.
- `ctx.runtime`: deadline, trace id, query id from Pipeline adapter paths, and host
  runtime metadata.
- `ctx.delivery`: current delivery surface and streaming/edit capabilities.
- `ctx.bootstrap`: optional host bootstrap payload.
- `ctx.adapter`: Pipeline adapter fields; not part of Protocol v1 core.

LangBot does not inline full history by default. If a runner needs more context,
it should use the authorized pull APIs, such as history, event, artifact, state,
or storage APIs.

## Plugins

| Plugin | Runner ID | Replaces | Description |
| --- | --- | --- | --- |
| `dify-agent` | `plugin:langbot/dify-agent/default` | `dify-service-api` | Dify application integration |
| `n8n-agent` | `plugin:langbot/n8n-agent/default` | `n8n-service-api` | n8n workflow webhook integration |
| `coze-agent` | `plugin:langbot/coze-agent/default` | `coze-api` | Coze (扣子) bot integration |
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
- Respect `ctx.resources` and use `AgentRunAPIProxy` for any Host-mediated
  model, tool, knowledge, history, event, artifact, state, or storage access.
- Use `ctx.context` to decide whether more history/artifact/state can be pulled.

Pipeline adapter fields are adapter-only:

- Business parameters may appear in `ctx.adapter.extra.params`.
- Prompt data, if present, may appear in `ctx.adapter.extra.prompt`.
- Bootstrap history may appear in `ctx.bootstrap.messages`.

Do not depend on top-level `ctx.params`, `ctx.prompt`, or `ctx.messages` as the
long-term Protocol v1 contract. New runner code should prefer event-first
fields and pull APIs.

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
