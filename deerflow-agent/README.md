# DeerFlow Agent

Official LangBot AgentRunner plugin for DeerFlow LangGraph HTTP API.

This plugin replaces the legacy `deerflow-api` provider runner and supports:

- LangGraph thread creation and stateful runs.
- `values`, `messages-tuple`, `messages`, `message`, `custom`, `error`, and `end` SSE events.
- Multimodal image input through image URLs and data URLs.
- External thread state persisted as `external.thread_id`.
