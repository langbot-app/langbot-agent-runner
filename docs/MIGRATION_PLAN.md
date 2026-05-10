# LangBot 官方 AgentRunner 插件迁移计划

本文档面向 Claude Code agent team。目标是在 `langbot-agent-runner` 仓库中承接 LangBot 旧内置 runner，把它们迁移为官方 AgentRunner 插件，并与 `langbot-plugin-sdk` 的 AgentRunner Protocol v1 对齐。

当前分支可以激进推进：不要求和旧 LangBot 内置 runner 路径长期并存；但最终必须支持 LangBot 侧把历史 Pipeline 配置迁移到新的 `ai.runner.id` 与 `ai.runner_config`。

## 1. 仓库目标

`langbot-agent-runner` 是官方 runner 插件仓库，不属于 LangBot host，也不属于 SDK runtime。

职责边界：

- 本仓库实现官方业务 runner 插件：Local Agent、Dify、n8n、Coze、DashScope、Langflow、Tbox。
- LangBot host 只负责发现 runner、构造 `AgentRunContext`、裁剪资源、调用 SDK runtime、归一结果。
- SDK 只负责 AgentRunner 组件接口、runtime discovery、`RUN_AGENT` 执行协议。
- 本仓库不得重新引入 LangBot host 的 Pipeline 内部对象作为运行时依赖。

禁止事项：

- 不要在 runner 中导入 `langbot.pkg.pipeline.query.Query`。
- 不要依赖 `query.pipeline_config['ai'][old_runner_name]`。
- 不要返回旧 `chunk/text/finish` 协议。
- 不要让插件绕过 `ctx.resources` 访问未授权模型、工具、知识库。

## 2. 依赖前置条件

迁移前确认 SDK 侧已满足：

- `langbot_plugin.api.definition.components.AgentRunner` 可用。
- `AgentRunContext`、`AgentRunResult`、`AgentRunnerCapabilities`、`AgentRunnerPermissions` 可用。
- `PluginManager.list_agent_runners()` 支持一个插件暴露多个 AgentRunner。
- `PluginManager.run_agent()` 只输出 v1 result：
  - `message.delta`
  - `message.completed`
  - `tool.call.started`
  - `tool.call.completed`
  - `state.updated`
  - `run.completed`
  - `run.failed`
  - `action.requested`
- SDK discovery 返回的 `manifest` 形态必须明确。建议返回 raw component manifest，而不是 `ComponentManifest` 包装对象。
- SDK 公共导出不要误删 `KnowledgeRetriever`、`PolymorphicComponent` 等既有组件导出。

LangBot host 侧对接前确认：

- Runner id 使用 `plugin:{author}/{plugin_name}/{runner_name}`。
- LangBot 构造 SDK v1 `AgentRunContext`。
- LangBot 按 SDK 当前 `AgentResources` 字段名构造资源。
- LangBot 只接收 v1 `AgentRunResult`。
- 历史配置由 LangBot host 迁移到 `ai.runner_config[runner_id]`。

## 3. 目标目录结构

本仓库应对齐 `langbot-plugin-demo` 的组织方式：仓库根目录下每个一级目录就是一个完整插件。不要使用 `packages/<plugin>` 作为默认结构，否则会和现有插件仓库、插件安装、marketplace 索引、人工调试路径形成额外差异。

仓库仍然是 monorepo，但 monorepo 的粒度是“根目录多个插件目录”，不是 Python packaging 风格的 `packages/`。

```text
langbot-agent-runner/
  README.md
  pyproject.toml
  local-agent/
    manifest.yaml
    components/
      agent_runner/
        default.yaml
        default.py
    pkg/
      __init__.py
      config.py
      rag.py
      tool_loop.py
    assets/
      icon.svg
    readme/
    README.md
    requirements.txt
    main.py
    tests/
  dify-agent/
    manifest.yaml
    components/agent_runner/default.yaml
    components/agent_runner/default.py
    pkg/
    assets/
    readme/
    README.md
    requirements.txt
    main.py
    tests/
  n8n-agent/
  coze-agent/
  dashscope-agent/
  langflow-agent/
  tbox-agent/
  _shared/
    langbot_agent_runner_utils/
  tests/
    contract/
    fixtures/
  scripts/
```

每个 runner 插件目录都应该能被单独复制、安装、加载。`_shared/` 只用于开发期同步 helper、生成模板和测试夹具；最终插件运行时不要依赖 `../_shared` 这种父目录 import。需要复用的 helper 应复制或 vendoring 到各插件自己的 `pkg/` 内，或者后续单独发布为正式依赖包。

插件目录内部尽量贴近 `langbot-plugin-demo`：

- `manifest.yaml` 声明插件。
- `main.py` 只放 `BasePlugin` 入口。
- `components/agent_runner/` 放 AgentRunner component manifest 和实现。
- `assets/`、`readme/`、`requirements.txt` 按现有插件生态保留。

## 4. Runner 映射

固定迁移映射如下：

| 旧 runner | 新官方插件 | runner id | 旧实现来源 |
| --- | --- | --- | --- |
| `local-agent` | `langbot/local-agent` | `plugin:langbot/local-agent/default` | `/home/glwuy/langbot-app/LangBot/src/langbot/pkg/provider/runners/localagent.py` |
| `dify-service-api` | `langbot/dify-agent` | `plugin:langbot/dify-agent/default` | `/home/glwuy/langbot-app/LangBot/src/langbot/pkg/provider/runners/difysvapi.py` |
| `n8n-service-api` | `langbot/n8n-agent` | `plugin:langbot/n8n-agent/default` | `/home/glwuy/langbot-app/LangBot/src/langbot/pkg/provider/runners/n8nsvapi.py` |
| `coze-api` | `langbot/coze-agent` | `plugin:langbot/coze-agent/default` | `/home/glwuy/langbot-app/LangBot/src/langbot/pkg/provider/runners/cozeapi.py` |
| `dashscope-app-api` | `langbot/dashscope-agent` | `plugin:langbot/dashscope-agent/default` | `/home/glwuy/langbot-app/LangBot/src/langbot/pkg/provider/runners/dashscopeapi.py` |
| `langflow-api` | `langbot/langflow-agent` | `plugin:langbot/langflow-agent/default` | `/home/glwuy/langbot-app/LangBot/src/langbot/pkg/provider/runners/langflowapi.py` |
| `tbox-app-api` | `langbot/tbox-agent` | `plugin:langbot/tbox-agent/default` | `/home/glwuy/langbot-app/LangBot/src/langbot/pkg/provider/runners/tboxapi.py` |

配置 schema 来源：

- 旧 Pipeline metadata：`/home/glwuy/langbot-app/LangBot/src/langbot/templates/metadata/pipeline/ai.yaml`
- 默认配置：`/home/glwuy/langbot-app/LangBot/src/langbot/templates/default-pipeline-config.json`
- 旧迁移逻辑参考：`/home/glwuy/langbot-app/LangBot/src/langbot/pkg/persistence/migrations/`

迁移时尽量保持旧配置字段名，降低 LangBot host 历史配置迁移成本。

## 5. AgentRunner 组件模板

每个插件至少包含一个 `default` runner。

`manifest.yaml` 示例：

```yaml
apiVersion: langbot/v1
kind: Plugin
metadata:
  author: langbot
  name: dify-agent
  label:
    en_US: Dify Agent
    zh_Hans: Dify Agent
  description:
    en_US: Run a Dify application as a LangBot AgentRunner.
    zh_Hans: 将 Dify 应用作为 LangBot AgentRunner 运行。
spec:
  version: 0.1.0
```

`components/agent_runner/default.yaml` 示例：

```yaml
apiVersion: langbot/v1
kind: AgentRunner
metadata:
  name: default
  label:
    en_US: Default
    zh_Hans: 默认
  description:
    en_US: Default AgentRunner.
    zh_Hans: 默认 AgentRunner。
spec:
  protocol_version: "1"
  config: []
  capabilities:
    streaming: true
    tool_calling: false
    knowledge_retrieval: false
    multimodal_input: false
    event_context: true
    platform_api: false
    interrupt: false
    stateful_session: true
  permissions:
    models: []
    tools: []
    knowledge_bases: []
    storage: ["plugin"]
    files: []
    platform_api: []
execution:
  python:
    path: ./main.py
    attr: DefaultAgentRunner
```

`main.py` 入口示例：

```python
from langbot_plugin.api.definition.components import AgentRunner
from langbot_plugin.api.entities.builtin.agent_runner import AgentRunContext, AgentRunResult
from langbot_plugin.api.entities.builtin.provider.message import Message


class DefaultAgentRunner(AgentRunner):
    async def run(self, ctx: AgentRunContext):
        text = ctx.input.to_text()
        yield AgentRunResult.message_completed(Message(role="assistant", content=text))
        yield AgentRunResult.run_completed()
```

实际代码应使用 SDK 的 `Message` / `MessageChunk` 实体构造 result。

## 6. 统一迁移规则

所有 runner 都遵循以下规则：

1. `ctx.config` 是当前 runner 的实例配置，来自 LangBot 的 `ai.runner_config[runner_id]`。
2. `ctx.input.to_text()` 是纯文本主输入。
3. `ctx.input.contents`、`ctx.input.attachments` 用于多模态输入和文件输入。
4. `ctx.messages` 是 LangBot host 已经整理好的历史消息。
5. `ctx.conversation` 只作为外部平台 session/conversation id 的来源之一。
6. 外部平台 conversation id 如果需要持久化，优先使用 plugin storage 或平台返回的 state，并通过 `state.updated` 暴露给 LangBot host。
7. 流式 runner 输出 `message.delta`，最后输出 `run.completed`。
8. 非流式 runner 输出 `message.completed`，最后输出 `run.completed`。
9. 业务异常不要吞掉后伪装成普通 assistant 文本；应输出或抛出，让 runtime 转为 `run.failed`。
10. 配置缺失应尽早失败，错误 code 使用 `runner.config_invalid` 或让 runtime 包装为 `runner.exception`。

## 7. `_shared` 开发期工具

先实现 `_shared/langbot_agent_runner_utils`，用于脚手架生成、测试夹具和同步 helper。注意：插件运行时不要直接 import `_shared`，因为单个插件被复制或安装后，其父目录通常不在插件运行环境内。

建议 helper：

- `get_text_input(ctx) -> str`
- `get_required_config(ctx, key) -> Any`
- `get_optional_config(ctx, key, default=None) -> Any`
- `message_completed(text) -> AgentRunResult`
- `message_delta(text, is_final=False, sequence=None) -> AgentRunResult`
- `run_failed(error, code) -> AgentRunResult`
- `http_timeout(config, default=120) -> aiohttp.ClientTimeout`
- `stable_user_id(ctx) -> str`
- `stable_conversation_id(ctx) -> str | None`

约束：

- helper 不应导入 LangBot host。
- helper 不应隐藏权限检查。
- 平台 SDK 封装放在各插件目录内，不放 `_shared`。

## 8. 分阶段执行

### Phase 0：仓库脚手架

交付：

- 根 `README.md`
- 根 `pyproject.toml`
- `_shared/langbot_agent_runner_utils` 开发期工具
- 七个根目录插件：`local-agent/`、`dify-agent/`、`n8n-agent/`、`coze-agent/`、`dashscope-agent/`、`langflow-agent/`、`tbox-agent/`
- 每个插件的 `manifest.yaml`、`components/agent_runner/default.yaml`、`components/agent_runner/default.py`、`main.py`
- 最小 contract tests

验收：

- 每个插件目录都能被 SDK runtime discovery 识别为 `AgentRunner`。
- discovery 返回 runner id 所需字段：author/name/runner_name/protocol_version/capabilities/permissions/config。
- 所有 stub runner 都能执行 `RUN_AGENT` 并返回 `message.completed` + `run.completed`。

### Phase 1：Dify 垂直切片

先迁 `dify-agent`，用它验证外部平台 runner 的完整开发模式。

来源：

- `/home/glwuy/langbot-app/LangBot/src/langbot/pkg/provider/runners/difysvapi.py`
- `ai.yaml` 中 `dify-service-api` 配置区。

要求：

- 支持 `chat`、`agent`、`workflow` 三种 app type。
- 保留旧字段：`base-url`、`app-type`、`api-key`、`base-prompt`、`thinking-convert`、`timeout`。
- 支持 Dify 流式响应转换为 `message.delta`。
- 支持非流式响应转换为 `message.completed`。
- 平台错误转 `run.failed`。

验收：

- mock Dify 服务测试覆盖 chat/agent/workflow。
- 流式测试验证 delta 顺序和最终 completed。
- 配置缺失测试验证失败路径。

### Phase 2：外部 workflow runner

并行迁移：

- `n8n-agent`
- `langflow-agent`

n8n 要求：

- 保留旧字段：`webhook-url`、`timeout`、`output-key`、`auth-type`、`basic-username`、`basic-password`、`jwt-secret`、`jwt-algorithm`、`header-name`、`header-value`。
- 继续兼容普通 JSON 响应和旧的 `type:item/type:end` 流式响应。
- payload 使用 `ctx.input.to_text()`、`ctx.conversation`、`ctx.runtime.metadata` 构造，不再依赖 `query.variables`。

Langflow 要求：

- 保留旧字段：`base-url`、`api-key`、`flow-id`、`input_type`、`output_type`、`tweaks`。
- `tweaks` 必须用 JSON parser 校验，不要用裸字符串拼接。
- 外部返回结构变化时提供清晰错误。

验收：

- HTTP mock 覆盖认证、timeout、普通 JSON、流式 JSON。
- conversation id 生成和传递有测试。

### Phase 3：平台 Agent API runner

并行迁移：

- `coze-agent`
- `dashscope-agent`
- `tbox-agent`

Coze 要求：

- 保留旧字段：`api-key`、`bot-id`、`timeout`、`auto_save_history`、`api-base`。
- 支持文本、图片、文件输入。多模态输入必须来自 `ctx.input.contents` 或 `ctx.input.attachments`。
- 思维链处理逻辑迁出 `pipeline_config['output']`，由 runner config 或 host metadata 显式传入。

DashScope 要求：

- 保留旧字段：`app-type`、`app-id`、`api-key`、`references_quote`。
- 支持 `agent`、`workflow`。
- 引用资料替换逻辑保留。
- 同步 SDK 如果是阻塞 SDK，必须隔离到 executor 或明确不可并发风险。

Tbox 要求：

- 保留旧字段：`app-id`、`api-key` 以及旧实现中使用的其它字段。
- 支持平台 session 状态。
- 平台错误必须保留 request id 或错误 code 到 `run.failed.data`。

验收：

- 每个平台至少有成功、平台错误、timeout、配置缺失测试。
- 支持流式的平台必须测试 `message.delta`。

### Phase 4：Local Agent

`local-agent` 是最复杂的迁移。它应最后作为能力完整性收口，也可以在 Phase 1 后单独开 worker 并行推进。

来源：

- `/home/glwuy/langbot-app/LangBot/src/langbot/pkg/provider/runners/localagent.py`
- LangBot preprocessor：`/home/glwuy/langbot-app/LangBot/src/langbot/pkg/pipeline/preproc/preproc.py`
- message truncate：`/home/glwuy/langbot-app/LangBot/src/langbot/pkg/pipeline/msgtrun/truncators/round.py`

要求：

- 支持主模型和 fallback models。
- 支持 prompt + history + 当前输入拼接。
- 支持 streaming / non-streaming。
- 支持 tool calling loop。
- 支持知识库检索和 rerank。
- 不再从 LangBot host 内部 manager 直接取模型、工具、知识库；只能使用 `ctx.resources` 暴露的资源和 SDK/host 提供的 proxy 能力。
- 如果 SDK 尚未提供模型调用、工具调用、知识库检索 proxy，本阶段先定义 adapter interface 和 contract tests，不要绕回 LangBot 内部对象。

验收：

- 模型 primary 成功。
- primary 失败后 fallback 成功。
- 全部模型失败返回 `run.failed`。
- streaming 首 chunk 成功后不再 fallback。
- knowledge retrieval + rerank 逻辑测试。
- tool call started/completed event 测试。
- 不配置模型时明确失败。

## 9. 配置迁移约定

LangBot host 负责把旧配置从：

```json
{
  "ai": {
    "runner": {
      "runner": "dify-service-api"
    },
    "dify-service-api": {
      "base-url": "...",
      "api-key": "..."
    }
  }
}
```

迁移为：

```json
{
  "ai": {
    "runner": {
      "id": "plugin:langbot/dify-agent/default",
      "expire-time": 0
    },
    "runner_config": {
      "plugin:langbot/dify-agent/default": {
        "base-url": "...",
        "api-key": "..."
      }
    }
  }
}
```

本仓库需要配合：

- 保持旧字段名。
- 在 component `spec.config` 中声明旧字段。
- 给每个插件提供 `DEFAULT_CONFIG` 或 schema 测试，方便 LangBot host metadata 展示。
- 不要要求 LangBot host 在迁移时改字段语义。

## 10. 测试策略

必须有三类测试：

1. Contract tests
   - discovery 能看到全部 runner。
   - 每个 runner 的 manifest 满足 protocol v1。
   - 每个 runner 的 `run()` 输出都能被 `AgentRunResult.model_validate()` 通过。

2. Unit tests
   - 配置解析。
   - 输入转换。
   - 外部平台响应解析。
   - 错误转换。

3. Integration-style tests
   - 使用 HTTP mock 或 fake platform client。
   - 不依赖真实 Dify/n8n/Coze/DashScope/Langflow/Tbox 服务。
   - Local Agent 使用 fake model/tool/kb resource。

建议命令：

```bash
uv run pytest
uv run ruff check .
```

如果仓库不用 `uv`，脚手架阶段先固定一种执行方式，并写入根 README。

## 11. 和 LangBot Host 的联调顺序

1. SDK runtime discovery 识别本仓库插件。
2. LangBot registry 能看到七个 runner descriptor。
3. LangBot pipeline metadata 能展示七个 runner。
4. 选择 `dify-agent` 跑通最小消息。
5. 选择 `n8n-agent` 跑通 webhook。
6. 选择 `local-agent` 跑通纯模型调用。
7. 再打开 Local Agent 的工具、知识库、fallback。
8. 最后执行历史配置迁移。

不要一开始就用 `local-agent` 做唯一联调对象。它覆盖面太大，容易把 SDK/host 协议问题和业务实现问题混在一起。

## 12. 给 Claude Code Team 的建议拆分

推荐并行 worker：

- Worker A：仓库脚手架、`_shared` 开发期工具、contract tests。
- Worker B：Dify vertical slice。
- Worker C：n8n + Langflow。
- Worker D：Coze + DashScope + Tbox。
- Worker E：Local Agent model/prompt/streaming。
- Worker F：Local Agent tools/knowledge/rerank。

分工约束：

- Worker A 先定义插件目录规范、测试夹具和 manifest 规范。
- 其它 worker 不要改 `_shared` public API，确需新增 helper 时先补测试，并同步到插件自己的 `pkg/`。
- 每个 worker 只修改自己负责的根目录插件，减少冲突。
- Local Agent 两个 worker 必须约定清楚写入文件，避免同时改同一个 `runner.py`。

## 13. 首轮 Claude Code Prompt

可以把下面这段作为第一次交给 CC team 的 prompt：

```text
你在 /home/glwuy/langbot-app/langbot-agent-runner 工作。请阅读 docs/MIGRATION_PLAN.md，以及：
- /home/glwuy/langbot-app/langbot-plugin-sdk/docs/agent-runner-pluginization/PROTOCOL_V1.md
- /home/glwuy/langbot-app/LangBot/docs/agent-runner-pluginization/IMPLEMENTATION_PLAN.md
- /home/glwuy/langbot-app/LangBot/docs/agent-runner-pluginization/OFFICIAL_RUNNER_PLUGINS.md

目标：先完成 Phase 0 仓库脚手架和 contract tests，不要开始迁移具体平台业务逻辑。

要求：
1. 创建根 README、pyproject、_shared 开发期工具，以及根目录下七个官方 runner 插件目录。
2. 每个插件包含 manifest.yaml、components/agent_runner/default.yaml、components/agent_runner/default.py、main.py 和最小 runner class。
3. 每个 runner 使用 AgentRunner Protocol v1，返回 message.completed + run.completed。
4. 添加 contract tests，验证七个 runner 的 manifest、runner id、capabilities、permissions、run result schema。
5. 不要导入 LangBot host 内部模块。
6. 不要改 LangBot 或 langbot-plugin-sdk 仓库。
7. 运行可用测试和 lint；如果缺依赖或 SDK 当前未安装，说明阻塞并给出最小修复建议。

完成后汇报：新增文件、测试命令、测试结果、遗留风险。
```

Phase 0 验收后，再分别给 Dify、n8n/Langflow、平台 API、Local Agent workers 下发迁移 prompt。

## 14. 最终验收标准

- 七个旧 runner 都有官方 AgentRunner 插件实现。
- 每个插件都能被 SDK runtime discovery。
- 每个 runner 都能通过 `RUN_AGENT` 输出 v1 result。
- 本仓库没有 LangBot host 内部模块依赖。
- 旧配置字段可无损进入新 `ai.runner_config[runner_id]`。
- 外部 API runner 有 mock 测试覆盖成功、错误、timeout。
- `local-agent` 覆盖模型 fallback、streaming、tool calling、knowledge retrieval、rerank。
- LangBot 主仓库删除或停用旧 `RequestRunner` 业务执行路径后，核心对话仍可通过官方插件完成。
