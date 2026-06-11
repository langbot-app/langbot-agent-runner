# LiteLLM Agent Platform Agent

LangBot AgentRunner plugin for LiteLLM Agent Platform.

Runner ID:

```text
plugin:langbot/litellm-agent-platform-agent/default
```

The runner reads the current input text from `ctx.input.to_text()`, prepends
LangBot run-scoped instructions, forwards it to a managed external agent
session, and returns the final assistant text as a LangBot assistant message.

## Runner Configuration

| Field | Default | Description |
| --- | --- | --- |
| `api-mode` | `agent-platform` | `agent-platform` calls `/api/v1/managed_agents/...`. `managed-agents-v0` calls lite-harness `/v1/sessions`. |
| `base-url` | empty | Base URL for the selected API. Required. |
| `api-key` | empty | Optional bearer token. Required by the full Agent Platform API when auth is enabled. |
| `agent-id` | empty | Agent Platform agent id. Required when `api-mode=agent-platform`. |
| `harness` | `claude-code` | lite-harness harness id, for example `claude-code`, `codex`, `pi-ai`, or another provider id. Used only in `managed-agents-v0`. |
| `model` | empty | Optional model for lite-harness session creation. |
| `title` | empty | Optional new session title. If empty, the first user input is truncated into a title. |
| `create-session-if-missing` | `true` | Create a platform session when no conversation-scoped external session id exists. |
| `session-ready-timeout` | `300` | Seconds to wait for an Agent Platform session to become `ready`. |
| `poll-interval` | `2` | Seconds between session/event polling requests. |
| `timeout` | `300` | Per-request timeout in seconds. |

## Behavior

In `agent-platform` mode, the runner stores the platform session id in
conversation state under `external.session_id`. It creates the session without an
initial prompt, waits until it is ready, then sends the current LangBot input
plus run-scoped MCP instructions to
`POST /api/v1/managed_agents/sessions/{session_id}/message`.

In `managed-agents-v0` mode, the runner stores the lite-harness session id under
`external.managed_session_id` and also mirrors it to `external.session_id` for
operator visibility. It sends one `user.message` event and polls event history
until `session.status_idle` or `session.status_error`.

`managed-agents-v0` exists only as a compatibility/debug path. Production use
should prefer the full Agent Platform API.

## LangBot MCP Gateway

Because upstream Agent Platform currently binds MCP servers at the agent level,
the plugin can start a stable HTTP MCP gateway that LiteLLM can register once
and attach to Agent Platform agents. The gateway itself is stable, but every MCP
tool call must include the current LangBot `run_id`. The runner injects that
`run_id` into the message it sends to LiteLLM.

Plugin-level gateway config:

| Field | Default | Description |
| --- | --- | --- |
| `mcp-gateway-enabled` | `false` | Start the HTTP MCP gateway. |
| `mcp-gateway-host` | `127.0.0.1` | Bind address. Use `0.0.0.0` only behind trusted network/proxy controls. |
| `mcp-gateway-port` | `8765` | Bind port. |
| `mcp-gateway-public-url` | empty | Reachable URL to register in LiteLLM, for example `https://langbot.example.com/mcp`. |
| `mcp-gateway-token` | empty | Required when enabled. Send as `Authorization: Bearer <token>` or `X-LangBot-MCP-Gateway-Token`. |
| `mcp-gateway-timeout` | `60` | Gateway request timeout in seconds. |

Register the gateway in LiteLLM as an HTTP MCP server and attach that server to
the Agent Platform agent. The Agent Platform agent then sees:

- `langbot_history_page`
- `langbot_retrieve_knowledge`
- `langbot_call_tool`

Each tool schema requires a `run_id` argument. Calls are forwarded through the
LangBot Host run authorization path, so an expired, wrong, cross-plugin, or
unauthorized `run_id` is rejected by Host. This is still weaker than true
session-scoped MCP injection because the model must copy the `run_id` from the
prompt, but it avoids global LangBot asset access while LiteLLM lacks
session-scoped MCP attachment.
