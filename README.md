# LangBot Official AgentRunner Plugins

This repository contains official AgentRunner plugins for LangBot, migrated from the legacy built-in runners.

## Overview

Each plugin directory is a standalone LangBot plugin that can be discovered, installed, and executed by the LangBot Plugin Runtime using AgentRunner Protocol v1.

## Plugins

| Plugin | Runner ID | Legacy Runner | Description |
| --- | --- | --- | --- |
| `local-agent` | `plugin:langbot/local-agent/default` | `local-agent` | Built-in agent with model fallback, tools, knowledge retrieval |
| `dify-agent` | `plugin:langbot/dify-agent/default` | `dify-service-api` | Dify application integration |
| `n8n-agent` | `plugin:langbot/n8n-agent/default` | `n8n-service-api` | n8n workflow webhook integration |
| `coze-agent` | `plugin:langbot/coze-agent/default` | `coze-api` | Coze (扣子) bot integration |
| `dashscope-agent` | `plugin:langbot/dashscope-agent/default` | `dashscope-app-api` | Aliyun DashScope (百炼) integration |
| `langflow-agent` | `plugin:langbot/langflow-agent/default` | `langflow-api` | Langflow flow integration |
| `tbox-agent` | `plugin:langbot/tbox-agent/default` | `tbox-app-api` | Ant Tbox (百宝箱) integration |

## Development

### Requirements

- Python 3.10+
- `uv` package manager (recommended)

### Setup

```bash
uv sync --dev
```

### Run Tests

```bash
uv run pytest
```

### Run Lint

```bash
uv run ruff check .
```

## Architecture

- Each plugin is a root-level directory (not `packages/<plugin>`)
- `_shared/` contains development-time utilities only; plugins do not depend on it at runtime
- All runners use AgentRunner Protocol v1

## Related

- [LangBot Plugin SDK](https://github.com/langbot-app/langbot-plugin-sdk) - Plugin development SDK and runtime
- [LangBot](https://github.com/langbot-app/LangBot) - Main LangBot application
- [AgentRunner Protocol v1](./docs/MIGRATION_PLAN.md) - Protocol specification