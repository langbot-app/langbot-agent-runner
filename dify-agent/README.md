# Dify Agent

Run a Dify application as a LangBot AgentRunner.

## Runner ID

`plugin:langbot/dify-agent/default`

## Configuration (Static)

Configuration is **static** and should not contain runtime state. Only the following static fields are supported:

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| base-url | string | yes | https://api.dify.ai/v1 | Dify API base URL |
| api-key | string | yes | '' | Dify API key |
| app-type | select | yes | chat | Application type (chat/agent/workflow) |
| base-prompt | string | no | "" | Default prompt for empty input |
| timeout | integer | no | 30 | Request timeout (seconds) |
| remove-think | boolean | no | false | Remove thinking tags from output |

**Note:** Do not put `conversation_id` in config. Use `AgentRunContext.state` for conversation state.

## Capabilities

- `streaming`: yes
- `multimodal_input`: yes
- `stateful_session`: yes

## Runtime State

Conversation state is managed through `AgentRunContext.state` and `AgentRunResult.state_updated`:

### Reading State

The runner reads `external.conversation_id` from scoped state:

```python
conversation_id = ctx.state.conversation.get("external.conversation_id")
```

Priority:
1. `ctx.state.conversation["external.conversation_id"]` (persistent state)
2. `ctx.conversation.conversation_id` (host-provided context)
3. Empty string (start new conversation)

### Updating State

The runner outputs `state.updated` with proper scope:

```python
yield AgentRunResult.state_updated(
    "external.conversation_id",
    dify_conversation_id,
    scope="conversation",
)
```

LangBot host persists this state and loads it on the next run.

## Workflow Inputs

Workflow inputs are passed through `AgentRunContext.params`:

```python
# Runner uses params as Dify inputs
inputs = dict(ctx.params or {})
```

Legacy input variables are derived from context:

| Variable | Source |
| --- | --- |
| langbot_user_message_text | `ctx.input.to_text()` |
| langbot_session_id | `ctx.conversation.session_id` or `ctx.run_id` |
| langbot_conversation_id | `ctx.state.conversation.get("external.conversation_id")` or fallback |
| langbot_msg_create_time | `ctx.params.get("msg_create_time")` if provided |

## Example Usage

### Chat/Agent Mode with Stateful Session

LangBot host provides state snapshot:

```python
ctx = AgentRunContext(
    run_id="run_001",
    input=AgentInput(text="Hello"),
    config={
        "base-url": "https://api.dify.ai/v1",
        "api-key": "app-xxx",
        "app-type": "chat",
    },
    state=AgentRunState(
        conversation={"external.conversation_id": "dify_conv_123"},
    ),
    params={},
)
```

Runner will use `dify_conv_123` as Dify conversation ID, maintaining session continuity.

### Workflow Mode with Custom Inputs

```python
ctx = AgentRunContext(
    run_id="run_002",
    input=AgentInput(text="Process this"),
    config={
        "base-url": "https://api.dify.ai/v1",
        "api-key": "app-yyy",
        "app-type": "workflow",
    },
    params={
        "custom_var": "value1",
        "workflow_input": "data",
    },
)
```

Runner passes `params` to Dify workflow inputs.

## Legacy Runner

Migrated from `dify-service-api` in LangBot.

### Key Changes from Legacy

1. **Config is static only**: No `conversation_id` in config
2. **State via protocol**: Use `ctx.state` and `state.updated`
3. **Inputs via params**: Use `ctx.params` for workflow inputs
4. **Scoped state**: `external.conversation_id` with `scope="conversation"`