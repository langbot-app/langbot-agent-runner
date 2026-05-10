# n8n Workflow Agent

Run an n8n workflow webhook as a LangBot AgentRunner.

## Runner ID

`plugin:langbot/n8n-agent/default`

## Configuration

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| webhook-url | string | yes | '' | n8n webhook URL |
| auth-type | select | yes | none | Authentication type (none/basic/jwt/header) |
| basic-username | string | no | '' | Basic auth username |
| basic-password | string | no | '' | Basic auth password |
| jwt-secret | string | no | '' | JWT secret |
| jwt-algorithm | string | no | HS256 | JWT algorithm |
| header-name | string | no | '' | Custom header name |
| header-value | string | no | '' | Custom header value |
| timeout | integer | no | 120 | Request timeout (seconds) |
| output-key | string | no | response | Response output key |

## Capabilities

- `stateful_session`: yes

## Legacy Runner

Migrated from `n8n-service-api` in LangBot.