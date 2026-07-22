# Claude Code Agent

## Overview

Run Claude Code CLI as a LangBot AgentRunner.

## Package information

- **Runner ID**: `plugin:langbot-team/ClaudeCodeAgent/default`
- **Version**: `0.1.2`
- **Repository**: [https://github.com/langbot-app/langbot-agent-runner](https://github.com/langbot-app/langbot-agent-runner)

## Capabilities

- **Enabled**: `streaming`, `tool calling`, `knowledge retrieval`, `steering`
- **Not declared**: `multimodal input`, `interrupt`

## Configuration

| Field | Type | Required | Default |
| --- | --- | --- | --- |
| `daemon-enabled` | `boolean` | No | false |
| `daemon-host` | `string` | No | `127.0.0.1` |
| `daemon-port` | `integer` | No | `8767` |
| `daemon-token` | `secret` | No | Empty |
| `location` | `select` | Yes | `local` |
| `workspace` | `string` | No | Empty |
| `command` | `string` | No | `claude` |
| `args-json` | `string` | No | `[]` |
| `env-json` | `string` | No | `{}` |
| `ssh-target` | `string` | No | Empty |
| `ssh-port` | `integer` | No | `22` |
| `daemon-id` | `string` | No | Empty |
| `timeout` | `integer` | No | `300` |
| `streaming` | `boolean` | No | true |
| `reuse-session` | `boolean` | No | true |
| `langbot-assets-enabled` | `boolean` | No | true |
| `mcp-bridge-transport` | `select` | No | `auto` |
| `mcp-servers-json` | `string` | No | `[]` |

## Host permissions

- **`tools`**: `detail`, `call`
- **`knowledge_bases`**: `retrieve`
- **`history`**: `page`

## Installation and usage

1. Install the plugin from the LangBot plugin marketplace.
2. Select the Runner ID below in the Pipeline AgentRunner selector.
3. Fill in connection settings from the table and store credentials in secret fields in the admin UI.

## Security and limitations

- The runner can use only LangBot resources authorized for the current run.
- Availability, model abilities, and rate limits depend on the external service.
- See the full Chinese README at the package root for advanced behavior and product-specific limitations.

- Follow-ups are injected between turns, not mid-token.
- Follow-up turns currently carry text only; attachments on follow-ups are not
  yet forwarded.
- Steering only applies when the run has a conversation scope; otherwise the
  runner transparently falls back to single-turn execution.

## Structured Interaction Status

The SDK and LangBot Host expose provider-neutral `interaction.requested` and
`interaction.submitted` events, but this runner does not declare that
capability yet. Its current `claude -p` transport closes stdin after the prompt,
while Claude permission requests require a response tied to the live process.
The runner must first gain a durable bidirectional daemon or a verified
checkpoint/resume mapping for one-time permission decisions. Enabling the
manifest flag alone would advertise confirmations that cannot be resumed
safely.
