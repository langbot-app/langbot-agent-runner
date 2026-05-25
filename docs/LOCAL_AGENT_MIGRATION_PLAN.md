# Historical Local-Agent Migration Plan

This document is archived. The official local agent runner now lives in the
separate repository:

```text
/home/glwuy/langbot-app/langbot-local-agent
```

Do not use this file as implementation guidance. The current source of truth is
the local-agent repository README, tests, and the LangBot AgentRunner Protocol v1
docs.

Current Protocol v1 rules:

- Use `ctx.input` for the current event input.
- Use `ctx.bootstrap.messages` only as an optional bootstrap window.
- Use `ctx.adapter.extra["prompt"]` for Pipeline adapter prompt metadata when
  present.
- Do not rely on top-level `ctx.messages`, `ctx.prompt`, or `ctx.params`.
- Include `ctx.run_id` in every `AgentRunResult` factory call.
