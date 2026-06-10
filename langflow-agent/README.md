# Langflow Agent

Run a Langflow flow as a LangBot AgentRunner.

## Runner ID

`plugin:langbot/langflow-agent/default`

## Configuration

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| base-url | string | yes | http://localhost:7860 | Langflow server URL |
| api-key | string | yes | '' | Langflow API key |
| flow-id | string | yes | '' | Flow ID |
| input-type | string | no | chat | Input type |
| output-type | string | no | chat | Output type |
| tweaks | json | no | {} | Flow tweaks |

## Legacy Runner

Migrated from `langflow-api` in LangBot.
