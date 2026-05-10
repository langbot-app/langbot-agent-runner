# DashScope Agent

Run an Aliyun DashScope application as a LangBot AgentRunner.

## Runner ID

`plugin:langbot/dashscope-agent/default`

## Configuration

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| app-type | select | yes | agent | Application type (agent/workflow) |
| api-key | string | yes | '' | DashScope API key |
| app-id | string | yes | '' | Application ID |
| references_quote | string | no | 参考资料来自: | Quote text for references |

## Capabilities

- `streaming`: yes
- `stateful_session`: yes

## Legacy Runner

Migrated from `dashscope-app-api` in LangBot.