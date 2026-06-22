# Tbox Agent

Run an Ant Tbox application as a LangBot AgentRunner.

## Runner ID

`plugin:langbot/tbox-agent/default`

## Configuration

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| api-key | string | yes | '' | Tbox API key |
| app-id | string | yes | '' | Application ID |

## LangBot Asset Callback — Not Supported

Unlike the Dify, n8n, DashScope, Coze, and Langflow runners, this runner does
**not** support the LangBot asset callback (the SDK Asset Gateway MCP flow), due
to a transport mismatch:

- Ant Tbox (蚂蚁百宝箱) can attach a self-deployed external MCP server, but only
  accepts a **SSE (Server-Sent Events) Endpoint URL** for it (the older MCP
  HTTP+SSE transport: a `GET` stream plus a separate `POST` message endpoint).
  See the official docs: ["一键部署和使用社区 MCP"](https://alipaytbox.yuque.com/sxs0ba/doc/createmcp)
  and ["使用 MCP 插件"](https://alipaytbox.yuque.com/sxs0ba/doc/nggo3d5z3yc6t0fn).
- The LangBot SDK Asset Gateway only implements the newer **Streamable HTTP**
  transport (`POST /mcp` → JSON; no `text/event-stream` GET stream). It is what
  the Dify/n8n/DashScope/Coze/Langflow runners use.

Because Tbox speaks SSE and the gateway speaks Streamable HTTP, the two cannot
connect as-is. There is also no documented per-run channel to inject a run-scoped
token into a Tbox MCP tool call (Tbox MCP plugin URLs are static at creation
time), unlike Dify inputs or Coze `custom_variables`.

Supporting Tbox would require either adding an SSE transport endpoint to the SDK
Asset Gateway (`langbot_plugin/api/agent_tools/asset_gateway.py`), or Tbox adding
Streamable HTTP support. Neither is implemented today.

## Legacy Runner

Migrated from `tbox-app-api` in LangBot.
