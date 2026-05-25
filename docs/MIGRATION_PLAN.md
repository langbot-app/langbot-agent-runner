# Historical Migration Plan

This document is archived. The official runner plugins are developed directly
against AgentRunner Protocol v1; there is no supported intermediate protocol or
compatibility layer for earlier unpublished designs.

Current implementation guidance lives in:

- [README.md](../README.md)
- Each plugin's local `README.md`
- LangBot host docs under `docs/agent-runner-pluginization/`

Current Protocol v1 rules for this repository:

- Do not depend on top-level `ctx.params`, `ctx.prompt`, or `ctx.messages`.
- Read adapter params from `ctx.adapter.extra["params"]` when the host provides
  Pipeline adapter metadata.
- Read bootstrap messages from `ctx.bootstrap.messages` only as an optional
  small window.
- Use `ctx.input` for the current event input.
- Include `ctx.run_id` in every `AgentRunResult` factory call.
- Use explicit state scopes in `AgentRunResult.state_updated(...)`.
