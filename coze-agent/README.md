# Coze Agent

Run a Coze bot as a LangBot AgentRunner.

## Runner ID

`plugin:langbot/coze-agent/default`

## Configuration

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| api-key | string | yes | '' | Coze API key |
| bot-id | string | yes | '' | Bot ID |
| api-base | select | yes | https://api.coze.cn | API base URL (CN or Global) |
| auto-save-history | boolean | no | true | Auto-save conversation history |
| timeout | number | no | 120 | Request timeout (seconds) |

## Capabilities

- `streaming`: yes
- `multimodal_input`: yes
- `stateful_session`: yes

## Legacy Runner

Migrated from `coze-api` in LangBot.