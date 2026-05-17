# Local Agent Runner 迁移计划（已迁出）

> 当前文档是历史计划，保留用于了解迁移背景。真实 `local-agent` 官方插件已经迁到相邻仓库 `/home/glwuy/langbot-app/langbot-local-agent`。后续实现、测试和 parity 收口应以该仓库的 README、测试和 `langbot-skills` 的 local-agent 覆盖矩阵为准。

本文档曾规划 `local-agent` 从 Phase 0 stub 迁移为真实官方 AgentRunner 的步骤。历史上三方最小联调曾通过：

```text
LangBot -> SDK runtime -> langbot-agent-runner/local-agent -> LangBot
```

验证输入 `1`，输出 `[stub] Echo: 1`。这说明协议主链路可用。

后续工作不再在本仓库推进。当前 SDK/Host 已经补齐本计划中提到的主要缺口：

- `AgentRunContext.prompt`：宿主侧有效 prompt。
- `ctx.input.contents`：结构化/多模态当前输入。
- `ctx.runtime.metadata.streaming_supported` 与 `remove_think`。
- `AgentRunAPIProxy.invoke_llm` / `invoke_llm_stream` 的 `remove_think` override。
- `AgentRunAPIProxy.invoke_rerank`，并按 run-scoped model resources 做授权校验。
- LangBot host action 通过 `run_id/query_id` 找回当前 Query，恢复工具、知识库、模型 extra args 等上下文能力。

以下章节保留原计划文本，不代表当前实现状态；其中关于缺少 rerank proxy、只做 Phase A、`ctx.config["prompt"]` 优先等判断已经被后续 SDK/Host/local-agent 实现取代。

## 1. 迁移目标

目标 runner id：

```text
plugin:langbot/local-agent/default
```

目标能力：

- 读取 `ctx.config` 中的 local-agent 配置。
- 使用 `ctx.messages`、`ctx.input` 构造模型请求。
- 通过 SDK/LangBot proxy 调用宿主模型、工具和知识库。
- 输出 SDK v1 `AgentRunResult`。
- 不依赖 LangBot host 内部模块。

禁止事项：

- 不要 import `langbot.pkg.*`。
- 不要 import 或构造 Pipeline `Query`。
- 不要读取 `query.pipeline_config`。
- 不要返回旧 `chunk/text/finish` 协议。
- 不要绕过 `ctx.resources` 访问未授权资源。

当前插件目录必须保持：

```text
local-agent/
  main.py
  manifest.yaml
  components/agent_runner/default.yaml
  components/agent_runner/default.py
  pkg/
```

`default.yaml` 的 execution 必须是：

```yaml
execution:
  python:
    path: default.py
    attr: DefaultAgentRunner
```

因为 SDK 的 `ComponentManifest.get_python_component_class()` 会把 `path` 解析为相对 component yaml 所在目录。

## 2. 旧实现参考

旧 LangBot 实现：

```text
/home/glwuy/langbot-app/LangBot/src/langbot/pkg/provider/runners/localagent.py
```

相关 LangBot 阶段：

```text
/home/glwuy/langbot-app/LangBot/src/langbot/pkg/pipeline/preproc/preproc.py
/home/glwuy/langbot-app/LangBot/src/langbot/pkg/pipeline/msgtrun/truncators/round.py
```

旧 local-agent 主要行为：

- 选择 primary model。
- 顺序尝试 fallback models。
- 拼接 prompt、history、当前用户输入。
- 支持 streaming / non-streaming。
- 支持模型 tool call。
- 支持 knowledge retrieval。
- 支持 rerank。
- 支持 remove-think。
- 流式 fallback 只允许在首 chunk 之前发生。

## 3. 现有 SDK/Host 能力评估

### 已够用

SDK/LangBot 现在已经有以下 proxy 能力，可支持 local-agent 主体迁移：

- LLM 非流式调用：`self.plugin.invoke_llm(...)`
- LLM 流式调用：`self.plugin.invoke_llm_stream(...)`
- 工具列表：`self.plugin.list_tools(...)`
- 工具详情：`self.plugin.get_tool_detail(...)`
- 工具调用：`self.plugin.call_tool(...)`
- query scoped 知识库列表：`QueryBasedAPIProxy.list_pipeline_knowledge_bases(...)`
- query scoped 知识库检索：`QueryBasedAPIProxy.retrieve_knowledge(...)`
- AgentRunner context：`ctx.messages`、`ctx.input`、`ctx.resources`、`ctx.config`

AgentRunner component 本身通过 `self.plugin` 访问插件级 LangBot API proxy。需要 query 级权限的 API，例如当前 pipeline 的知识库检索，应基于 `ctx.runtime.query_id` 构造 `QueryBasedAPIProxy`：

```python
from langbot_plugin.api.proxies.query_based_api import QueryBasedAPIProxy

query_api = QueryBasedAPIProxy(
    query_id=ctx.runtime.query_id,
    plugin_runtime_handler=self.plugin.plugin_runtime_handler,
)
```

如果 `ctx.runtime.query_id` 为空，local-agent 不应退回到全局 `self.plugin.retrieve_knowledge(...)`，而应跳过 RAG 或返回清晰的配置/运行错误。

### 需要注意

1. rerank 直接能力不足  
   旧实现直接用 LangBot `model_mgr.get_rerank_model_by_uuid()`。插件侧目前没有清晰的 `invoke_rerank` proxy。Phase C 可以先跳过 rerank，Phase D 再决定补 SDK/Host proxy。

2. 模型能力信息不完整  
   `ctx.resources.models` 当前只有 `model_id/model_type/provider`。旧实现依赖模型 abilities 判断 `func_call`、`vision`。Phase A 先按配置尝试调用；Phase B/C 再补更精细的能力判断或要求 LangBot resource builder 暴露能力摘要。

3. 工具调用要通过 proxy  
   插件不能使用旧的 `self.ap.tool_mgr.execute_func_call()`。必须通过 SDK proxy `call_tool`，并把结果拼回 `role="tool"` 消息。

4. 知识库必须使用 pipeline scoped API  
   不要使用 unrestricted `list_knowledge_bases/retrieve_knowledge`。local-agent 只能检索当前 pipeline 授权范围内的知识库。

### 工作量预估与够用性判断

总体判断：转为插件形式是够用的，模型调用主路径不需要再改 SDK。Phase A 的真实模型调用可以直接基于现有 `invoke_llm` / `invoke_llm_stream` 完成。工具调用和 RAG 也有可用 proxy，但需要把旧 LangBot 内部对象调用改成协议化调用。真正不够的是 rerank、模型能力摘要和少数行为细节，这些不阻塞先把 local-agent 作为可用插件跑起来。

预估工作量按“熟悉当前三仓协议的实现者”计算：

| 阶段 | 范围 | 预估 |
| --- | --- | --- |
| Phase A | 真实模型调用、prompt/history/input 拼接、stream/non-stream、fallback、单测 | 0.5-1.5 天 |
| Phase B | tool schema 拉取、tool call loop、工具结果回填、循环上限、单测 | 1-2 天 |
| Phase C | pipeline scoped RAG、上下文拼接、失败策略、单测 | 0.5-1 天 |
| Phase D | rerank、多模态、remove-think、旧行为细节对齐 | 1-3 天，取决于是否补 SDK/Host proxy |
| 联调收尾 | LangBot 三方联调、配置迁移确认、文档修正 | 0.5-1 天 |

建议先让 Claude Code 只做 Phase A。Phase A 完成后就能把 `[stub] Echo` 替换成真实模型回答，足够验证插件化 local-agent 的核心可行性。Phase B/C 可以继续在 runner 仓库内推进；Phase D 发现缺口时再回 SDK/Host 补协议。

## 4. 配置约定

保持旧字段名，便于 LangBot migration 无损复制：

```yaml
model:
  primary: ""
  fallbacks: []
max-round: 10
prompt:
  - role: system
    content: "You are a helpful assistant."
knowledge-bases: []
rerank-model: ""
rerank-top-k: 5
```

兼容旧模型配置：

- `model` 是字符串时，视为 primary model id。
- `model.primary` 是主模型。
- `model.fallbacks` 是候选 fallback 模型列表。
- `__none__` 视为空值。

## 5. 分阶段迁移

### Phase A：模型调用 MVP

目标：替换 stub，跑通真实 LLM 调用。

范围：

- 解析 `ctx.config["model"]`。
- 构造模型候选列表：primary + fallbacks。
- 拼接请求消息：
  - `ctx.config["prompt"]`
  - `ctx.messages`
  - 当前 `ctx.input`
- 支持 non-streaming。
- 支持 streaming。
- 输出：
  - streaming：`message.delta` + `run.completed`
  - non-streaming：`message.completed` + `run.completed`
- fallback：
  - non-streaming：当前模型失败后尝试下一个。
  - streaming：首 chunk 前失败可 fallback，首 chunk 后失败不可换模型。

暂不做：

- tool call loop
- knowledge retrieval
- rerank
- 多模态特殊处理

建议文件：

```text
local-agent/components/agent_runner/default.py
local-agent/pkg/config.py
local-agent/pkg/messages.py
local-agent/pkg/model_calling.py
local-agent/tests/test_model_calling.py
```

验收：

- 未配置模型时返回 `run.failed` 或抛出受控异常。
- primary 成功时不调用 fallback。
- primary 失败 fallback 成功。
- 全部模型失败返回失败。
- streaming 首 chunk 前失败会 fallback。
- streaming 首 chunk 后失败不会 fallback。
- `uv run pytest -q` 和 `uv run ruff check .` 通过。

### Phase B：Tool Calling

目标：恢复旧 local-agent 的工具调用循环。

范围：

- 从模型返回 `tool_calls`。
- 解析 function name 和 JSON arguments。
- 通过 SDK proxy 调用工具。
- 输出 telemetry result：
  - `tool.call.started`
  - `tool.call.completed`
- 把工具结果追加为 `role="tool"` 消息。
- 继续调用模型直到没有 tool calls 或达到安全上限。

约束：

- 工具调用只允许使用 `ctx.resources.tools` 中可见的工具。
- 工具不存在、JSON 参数非法、工具执行失败都要进入可控失败或作为 tool result 返回给模型，具体策略要测试固定。
- 设置最大工具循环轮数，避免死循环。

建议文件：

```text
local-agent/pkg/tool_loop.py
local-agent/tests/test_tool_loop.py
```

验收：

- 单次工具调用成功。
- 多次工具调用成功。
- 工具参数 JSON 不合法。
- 工具不存在或未授权。
- 工具执行失败。
- 达到最大工具循环轮数。

### Phase C：Knowledge Retrieval

目标：恢复旧 local-agent 的 RAG 拼接能力。

范围：

- 从 `ctx.config["knowledge-bases"]` 读取知识库 id。
- 或从 `ctx.resources.knowledge_bases` 使用 LangBot 已授权的知识库列表。
- 使用基于 `ctx.runtime.query_id` 构造的 `QueryBasedAPIProxy.retrieve_knowledge` 检索。
- 把检索结果拼入用户消息，格式可以先沿用旧模板：

```text
The following are relevant context entries retrieved from the knowledge base.
Please use them to answer the user's message.
Respond in the same language as the user's input.

<context>
...
</context>

<user_message>
...
</user_message>
```

暂不做：

- rerank
- 文件级引用展示
- 精细 citation 格式

建议文件：

```text
local-agent/pkg/rag.py
local-agent/tests/test_rag.py
```

验收：

- 无知识库时不改变用户消息。
- 单知识库检索成功。
- 多知识库检索成功。
- 检索失败时策略明确：跳过并记录，或失败整个 run。
- 只使用 query/pipeline scoped API。
- `ctx.runtime.query_id` 缺失时不会调用全局知识库 API。

### Phase D：Rerank、多模态和行为对齐

目标：补齐与旧 local-agent 的主要差异。

范围：

- rerank：
  - 如果 SDK/Host 已补 `invoke_rerank` proxy，则恢复 rerank。
  - 如果没有，则保留配置但记录 warning，不执行 rerank。
- 多模态：
  - 文本 + 图片输入结构处理。
  - 根据模型能力决定是否传图；如果能力未知，先尝试并在失败时给出清晰错误。
- remove-think：
  - 从 config 或 runtime metadata 读取策略。
  - 保持旧输出行为。
- observability：
  - 模型使用、fallback、tool call、RAG 检索数量的 debug 信息。

验收：

- rerank 有能力时生效。
- 没有 rerank proxy 时不影响普通 RAG。
- 图片输入在支持模型上可传递。
- remove-think 行为有测试。

## 6. SDK/Host 缺口清单

迁移过程中如果遇到这些问题，不要在 runner 里绕过协议，应记录并回到 SDK/Host 补能力：

- 缺 `invoke_rerank`。
- `ctx.resources.models` 缺 abilities/extra_args。
- tool detail 不能返回 LLM 所需 JSON schema。
- query scoped knowledge API 不能按当前 pipeline 权限正确过滤。
- streaming LLM proxy 不能稳定透传 `MessageChunk.tool_calls`。

## 7. 测试策略

单测使用 fake proxy，不访问真实模型服务。

建议构造：

- `FakePluginAPI`
  - `invoke_llm`
  - `invoke_llm_stream`
  - `call_tool`
  - `retrieve_knowledge`
- `FakeAgentRunContext`
  - 真实 `AgentRunContext` 实体
  - 配置不同 model/tool/kb 场景

必须覆盖：

- model config parsing
- message building
- non-streaming primary success
- non-streaming fallback success
- streaming primary success
- streaming fallback before first chunk
- streaming error after first chunk
- no model configured
- malformed model response

Phase B/C/D 按各阶段增加测试。

## 8. 联调步骤

Phase A 完成后，按以下方式联调：

1. 安装或加载 `local-agent` 插件。
2. LangBot pipeline 选择：

```text
plugin:langbot/local-agent/default
```

3. 配置主模型，例如：

```json
{
  "ai": {
    "runner": {
      "id": "plugin:langbot/local-agent/default",
      "expire-time": 0
    },
    "runner_config": {
      "plugin:langbot/local-agent/default": {
        "model": {
          "primary": "<model_uuid>",
          "fallbacks": []
        },
        "max-round": 10,
        "prompt": [
          {"role": "system", "content": "You are a helpful assistant."}
        ],
        "knowledge-bases": [],
        "rerank-model": "",
        "rerank-top-k": 5
      }
    }
  }
}
```

4. 发送普通文本消息。
5. 预期不再返回 `[stub] Echo: ...`，而是模型真实回答。

## 9. 给实现 Agent 的首轮 Prompt

```text
你在 /home/glwuy/langbot-app/langbot-agent-runner 工作。请阅读 docs/LOCAL_AGENT_MIGRATION_PLAN.md 和 docs/MIGRATION_PLAN.md。

目标：只实现 Local Agent Phase A：真实模型调用 MVP。不要做工具调用、知识库、rerank、多模态高级处理。

范围：
- 只修改 local-agent/ 下文件和必要测试。
- 不修改 LangBot。
- 不修改 langbot-plugin-sdk。
- 保持组件路径：local-agent/components/agent_runner/default.py。

实现：
1. 把 local-agent/components/agent_runner/default.py 从 stub 改为真实模型调用。
2. 从 ctx.config 解析 model 配置，支持字符串旧格式和 {primary, fallbacks} 新格式。
3. 用 ctx.config.prompt + ctx.messages + ctx.input 构造请求消息。
4. 通过插件 SDK proxy 调用宿主 LLM：
   - non-streaming 用 self.plugin.invoke_llm
   - streaming 用 self.plugin.invoke_llm_stream
5. 实现 primary/fallback：
   - non-streaming 失败尝试下一个
   - streaming 首 chunk 前失败尝试下一个
   - streaming 首 chunk 后失败直接失败
6. 输出 AgentRunResult v1：
   - message.completed 或 message.delta
   - run.completed
   - 失败用 run.failed 或抛出受控异常交给 runtime 转换
7. 补单测，使用 fake proxy，不访问真实模型服务。

验收：
- uv run pytest -q
- uv run ruff check .
- ComponentManifest 加载测试仍通过。

完成后汇报：
- 改动文件
- 支持的能力
- 暂未支持的能力
- 测试结果
- 是否需要 SDK/Host 补能力
```
