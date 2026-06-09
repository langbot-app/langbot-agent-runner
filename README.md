# LangBot 官方 AgentRunner 插件

本仓库包含 LangBot 官方维护的外部服务 AgentRunner 插件。每个插件负责把第三方智能体、工作流或应用平台接入 LangBot AgentRunner 协议 v1。

这些插件是协议消费者。LangBot 宿主端负责运行封套、资源授权、事实存储、拉取接口、结果归一化和投递生命周期；插件只负责对应服务的请求/响应映射和状态交接。

## 仓库边界

本仓库不实现 LangBot EventGateway、事件订阅、事件通知、调度器或事件分发。这些系统属于 LangBot 宿主端或独立的事件相关分支。

本仓库中的插件消费 LangBot 传入的 `AgentRunContext`：

- `ctx.event`：事件优先的触发元数据。
- `ctx.conversation`、`ctx.actor`、`ctx.subject`：当前运行范围的元数据。
- `ctx.input`：当前用户或事件输入，包含多模态内容和制品 / 文件引用。
- `ctx.context`：上下文访问句柄、游标、内联策略和可用的拉取接口。
- `ctx.resources`：当前运行范围内已授权的模型、工具、知识库、文件和存储能力。
- `ctx.state`：宿主端投影给当前运行的小型状态。
- `ctx.runtime`：截止时间、追踪标识、协议版本、迁移适配路径中的查询标识，以及宿主端运行时元数据。
- `ctx.delivery`：当前投递面和流式输出 / 编辑能力。
- `ctx.config`：运行器绑定配置。
- `ctx.adapter`：迁移适配字段；它不是协议 v1 核心字段，也不应承载提示词、历史、RAG 结果、工具结构或已授权资源。

LangBot 默认不会内联完整历史。如果运行器需要更多上下文，应使用已授权的拉取接口，例如历史、事件、制品、状态或存储接口。

## 外部执行器访问 LangBot 资产

进程内运行器，例如 `local-agent`，应直接调用 `AgentRunAPIProxy`。

进程外执行器运行器，例如 Claude Code 和 Codex，可以按需启用 SDK 负责的 LangBot MCP 桥接服务。该桥接服务由 `AgentRunner` 基类按单次运行创建，只暴露当前运行授权范围内、由注解标记的 `AgentRunExternalTools` 子集，并通过 `AgentRunAPIProxy` 把所有 LangBot 资产访问委托回宿主端；运行器子进程退出后桥接服务会停止。

这不是全局 LangBot MCP 服务，运行器插件也不需要手写维护 LangBot 工具结构。SDK 负责注解、结构生成、标准输入输出 MCP 代理和 MCP 配置合并辅助逻辑；单个运行器只决定是否启用桥接服务，以及如何把生成的 MCP 配置传给自己的执行器。如果某个执行器需要 `langbot_history_page` 等桥接工具，对应运行器清单必须申请匹配的 AgentRunner 权限，LangBot 宿主端也必须为本次运行授权。

## 远端执行守护进程

代码执行器运行器可以复用无第三方依赖的 `remote_agent_daemon` 包来执行远端任务。远端机器应安装本仓库，不要手动复制守护进程源码文件：

```bash
python -m venv .venv
. .venv/bin/activate
pip install "git+https://github.com/langbot-app/langbot-agent-runner.git@main"
```

然后启动守护进程，并通过 `--agent` 选择适配器，例如：

```bash
python -m remote_agent_daemon \
  --agent codex \
  --host 0.0.0.0 \
  --port 8766 \
  --base-dir /path/to/langbot-remote-workspaces \
  --command-path /home/agent-user/.local/bin \
  --token "$LANGBOT_REMOTE_AGENT_TOKEN"
```

守护进程负责 HTTP 鉴权、工作区物化、子进程执行和结果传输。智能体相关行为放在小型命令适配器中，例如 `claude-code` 和 `codex`；未来接入 pi、kimi 等执行器时，应新增适配器，而不是复制守护进程。

## 插件列表

| 插件 | 运行器标识 | 替代对象 | 说明 |
| --- | --- | --- | --- |
| `dify-agent` | `plugin:langbot/dify-agent/default` | `dify-service-api` | Dify 应用集成 |
| `n8n-agent` | `plugin:langbot/n8n-agent/default` | `n8n-service-api` | n8n 工作流 webhook 集成 |
| `coze-agent` | `plugin:langbot/coze-agent/default` | `coze-api` | Coze（扣子）机器人集成 |
| `claude-code-agent` | `plugin:langbot/claude-code-agent/default` | - | 本地 Claude Code CLI 集成 |
| `codex-agent` | `plugin:langbot/codex-agent/default` | - | 本地 Codex CLI 集成 |
| `dashscope-agent` | `plugin:langbot/dashscope-agent/default` | `dashscope-app-api` | 阿里云 DashScope（百炼）集成 |
| `langflow-agent` | `plugin:langbot/langflow-agent/default` | `langflow-api` | Langflow 流程集成 |
| `tbox-agent` | `plugin:langbot/tbox-agent/default` | `tbox-app-api` | 蚂蚁 Tbox（百宝箱）集成 |

官方 `local-agent` 运行器维护在相邻的 `langbot-local-agent` 仓库中，因为它会直接调用 LangBot 托管的模型和工具，并拥有独立的测试面。

## 协议 v1 对齐

外部服务运行器通常把 LangBot 输入映射为远端平台调用：

- 从 `ctx.input` 读取文本和多模态输入。
- 当目标平台需要时，从 `ctx.event`、`ctx.actor` 和 `ctx.subject` 读取事件、操作者和对象元数据。
- 从 `ctx.delivery` 和 `ctx.runtime` 读取投递和运行时决策。
- 从 `ctx.config` 读取静态运行器绑定配置。
- 尊重 `ctx.resources`，并通过 `AgentRunAPIProxy` 访问任何由宿主端代理的模型、工具、知识、历史、事件、制品、状态或存储。
- 使用 `ctx.context` 判断是否可以拉取更多历史、制品或状态。

Pipeline 适配字段只用于适配层：

- 业务参数可以出现在 `ctx.adapter.extra.params`。
- 不应从 `ctx.adapter.extra.prompt` 读取提示词数据。
- 如果运行器有静态绑定提示词，这类配置属于 `ctx.config.prompt`。如果需要有效提示词 / 指令数据，应在 `ctx.context.available_apis.prompt_get` 可用时通过宿主端提示词接口拉取。
- 历史不会通过 `ctx.bootstrap` 投递；需要更多上下文时应使用已授权的历史拉取接口。

不要依赖顶层 `ctx.params`、`ctx.prompt`、`ctx.messages` 或 `ctx.bootstrap` 作为协议 v1 字段。新的运行器代码应优先使用事件优先字段和拉取接口。

第三方智能体平台通常有自己的提示词、应用、机器人或工作流配置。除非某个具体平台集成明确需要这种映射，运行器不应把 LangBot 提示词重新解释为第三方平台提示词。

## 状态和历史

外部会话标识、会话期标识、工作流运行标识和检查点应存储在插件存储或宿主端状态接口中。运行器不应依赖 LangBot 内部会话 UUID 结构作为私有实现细节。

推荐模式：

- 使用 `AgentRunAPIProxy.state_set(...)` 等宿主端状态接口或插件存储保存外部会话期标识。
- 只在需要时分页或搜索转录历史。
- 大型载荷应保存为制品，并通过制品接口读取。
- 当运行器希望 LangBot 持久化小型 JSON 状态时，返回 `state.updated`。
- 当运行器生成文件或大型输出时，返回 `artifact.created`。

## 开发

### 环境要求

- Python 3.10+
- `uv` 包管理器

### 安装依赖

```bash
uv sync --dev
```

### 运行测试

```bash
uv run pytest
```

### 运行代码检查

```bash
uv run ruff check .
```

## 架构

- 每个插件都是仓库根目录下的独立目录，不使用 `packages/<plugin>` 结构。
- 本仓库作为插件集合分发，不作为可导入的 `langbot_agent_runner` Python 包使用。
- 每个插件声明一个或多个 AgentRunner 组件。
- 所有运行器都使用 AgentRunner 协议 v1。
- 宿主端授权限定在单次运行范围内，并通过 `run_id`、`ctx.resources` 和调用方插件身份执行校验。
- Pipeline 适配转换由 LangBot 宿主端在调用运行器前完成；本仓库不拥有 Pipeline 内部逻辑。

## 相关仓库

- [LangBot Plugin SDK](https://github.com/langbot-app/langbot-plugin-sdk)：插件开发 SDK 和运行时。
- [LangBot](https://github.com/langbot-app/LangBot)：LangBot 主应用和 Host 实现。
- AgentRunner Protocol v1：见 LangBot 仓库中的 `LangBot/docs/agent-runner-pluginization/PROTOCOL_V1.md`。
