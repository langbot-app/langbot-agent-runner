# Dify Agent

Run a Dify application as a LangBot AgentRunner.

## Runner ID

`plugin:langbot/dify-agent/default`

## Configuration

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| base-url | string | yes | https://api.dify.ai/v1 | Dify API base URL |
| base-prompt | string | yes | "When the file..." | Default prompt for empty input |
| app-type | select | yes | chat | Application type (chat/agent/workflow) |
| api-key | string | yes | '' | Dify API key |
| timeout | integer | no | 30 | Request timeout (seconds) |

## Capabilities

- `streaming`: yes
- `multimodal_input`: yes
- `stateful_session`: yes

## Legacy Runner

Migrated from `dify-service-api` in LangBot.