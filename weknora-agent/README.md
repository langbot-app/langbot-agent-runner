# WeKnora Agent

Official LangBot AgentRunner plugin for WeKnora.

This plugin replaces the legacy `weknora-api` provider runner and supports:

- WeKnora Agent mode through `/agent-chat/{session_id}`.
- WeKnora knowledge-base chat mode through `/knowledge-chat/{session_id}`.
- Stateful external sessions persisted as `external.session_id`.
- Streaming output when the delivery surface supports it.
