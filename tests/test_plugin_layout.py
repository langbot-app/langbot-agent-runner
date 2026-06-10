from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import threading
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIRS = {
    "claude-code-agent",
    "codex-agent",
    "coze-agent",
    "dashscope-agent",
    "dify-agent",
    "langflow-agent",
    "n8n-agent",
    "tbox-agent",
}


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_runner_module(plugin_dir: str):
    for module_name in list(sys.modules):
        if module_name == "pkg" or module_name.startswith("pkg."):
            del sys.modules[module_name]

    plugin_root = ROOT / plugin_dir
    sys.path.insert(0, str(plugin_root))
    try:
        module_path = plugin_root / "components" / "agent_runner" / "default.py"
        spec = importlib.util.spec_from_file_location(f"test_{plugin_dir.replace('-', '_')}_runner", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(plugin_root))


def _load_claude_code_daemon_module():
    module_path = ROOT / "claude-code-agent" / "pkg" / "remote_daemon.py"
    spec = importlib.util.spec_from_file_location("test_claude_code_remote_daemon", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _load_codex_daemon_module():
    module_path = ROOT / "codex-agent" / "pkg" / "remote_daemon.py"
    spec = importlib.util.spec_from_file_location("test_codex_remote_daemon", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _load_shared_daemon_module():
    module_path = ROOT / "remote_agent_daemon" / "core.py"
    spec = importlib.util.spec_from_file_location("test_remote_agent_daemon_core", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_official_external_runner_plugins_have_protocol_v1_manifests() -> None:
    for plugin_dir in PLUGIN_DIRS:
        manifest = _load_yaml(ROOT / plugin_dir / "manifest.yaml")
        runner = _load_yaml(ROOT / plugin_dir / "components" / "agent_runner" / "default.yaml")

        assert manifest["metadata"]["author"] == "langbot"
        assert manifest["metadata"]["name"] == plugin_dir
        assert runner["apiVersion"] == "langbot/v1"
        assert runner["kind"] == "AgentRunner"
        assert runner["metadata"]["name"] == "default"
        assert runner["metadata"]["label"]["en_US"] != "Default"
        assert runner["metadata"]["label"]["zh_Hans"] != "默认"
        assert "protocol_version" not in runner["spec"]
        assert runner["execution"]["python"]["path"] == "default.py"
        assert runner["execution"]["python"]["attr"] == "DefaultAgentRunner"


def test_readme_lists_code_runner_ids() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "`claude-code-agent`" in readme
    assert "`plugin:langbot/claude-code-agent/default`" in readme
    assert "`codex-agent`" in readme
    assert "`plugin:langbot/codex-agent/default`" in readme


def test_code_runner_manifests_expose_only_product_level_config() -> None:
    expected_config_names = {
        "claude-code-agent": [
            "execution-mode",
            "remote-endpoint",
            "remote-token",
            "working-directory",
            "model",
            "dangerously-skip-permissions",
            "timeout",
        ],
        "codex-agent": [
            "execution-mode",
            "remote-endpoint",
            "remote-token",
            "working-directory",
            "model",
            "timeout",
        ],
    }
    for plugin_dir in {"claude-code-agent", "codex-agent"}:
        runner = _load_yaml(ROOT / plugin_dir / "components" / "agent_runner" / "default.yaml")
        config = runner["spec"]["config"]
        config_names = [item["name"] for item in config]

        assert config_names == expected_config_names[plugin_dir]
        show_if = {item["name"]: item.get("show_if") for item in config}
        assert show_if["remote-endpoint"] == {"field": "execution-mode", "operator": "eq", "value": "remote"}
        assert show_if["remote-token"] == {"field": "execution-mode", "operator": "eq", "value": "remote"}
        assert show_if["working-directory"] == {"field": "execution-mode", "operator": "eq", "value": "local"}
        assert not {
            "skills-json",
            "mcp-config-json",
            "mcp-config-file",
            "enable-langbot-mcp",
            "tools",
            "allowed-tools",
            "disallowed-tools",
            "max-turns",
            "dry-run",
            "mock-response",
        }.intersection(config_names)


def test_repository_builds_as_plugin_collection_not_import_package() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel_target = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]

    assert not (ROOT / "langbot_agent_runner").exists()
    assert set(wheel_target["only-include"]) == PLUGIN_DIRS | {"docs", "remote_agent_daemon"}


def test_code_runners_declare_bridge_related_capabilities() -> None:
    for plugin_dir in {"claude-code-agent", "codex-agent"}:
        runner = _load_yaml(ROOT / plugin_dir / "components" / "agent_runner" / "default.yaml")
        assert runner["spec"]["permissions"] == {
            "tools": ["detail", "call"],
            "knowledge_bases": ["retrieve"],
            "history": ["page"],
        }
        assert runner["spec"]["capabilities"]["tool_calling"] is True
        assert runner["spec"]["capabilities"]["knowledge_retrieval"] is True


def test_external_service_runners_declare_minimal_plugin_storage_permission() -> None:
    for plugin_dir in PLUGIN_DIRS - {"claude-code-agent", "codex-agent"}:
        runner = _load_yaml(ROOT / plugin_dir / "components" / "agent_runner" / "default.yaml")
        assert runner["spec"]["permissions"] == {"storage": ["plugin"]}


def test_runner_sources_do_not_read_capabilities_from_context() -> None:
    for plugin_dir in PLUGIN_DIRS:
        source = (ROOT / plugin_dir / "components" / "agent_runner" / "default.py").read_text(encoding="utf-8")
        assert "ctx.capabilities" not in source


def test_tbox_manifest_matches_runner_capabilities() -> None:
    runner = _load_yaml(ROOT / "tbox-agent" / "components" / "agent_runner" / "default.yaml")
    capabilities = runner["spec"]["capabilities"]

    assert capabilities["streaming"] is True
    assert capabilities["multimodal_input"] is True


def test_multimodal_runners_decode_data_url_attachments_and_derive_from_contents() -> None:
    for plugin_dir in {"coze-agent", "dify-agent", "tbox-agent"}:
        module = _load_runner_module(plugin_dir)

        assert module._decode_content("data:text/plain;base64,aGk=") == b"hi"

        attachments = module._attachments_from_contents(
            [
                {
                    "type": "file_base64",
                    "file_base64": "data:text/plain;base64,aGk=",
                    "file_name": "hello.txt",
                }
            ]
        )

        assert attachments == [
            {
                "type": "file",
                "name": "hello.txt",
                "content": "data:text/plain;base64,aGk=",
                "content_type": "text/plain",
            }
        ]


def test_runners_use_protocol_v1_actor_fields_for_user_identity() -> None:
    from langbot_plugin.api.entities.builtin.agent_runner import (
        ActorContext,
        AgentEventContext,
        AgentInput,
        AgentResources,
        AgentRunContext,
        AgentRuntimeContext,
        AgentTrigger,
        DeliveryContext,
    )

    ctx = AgentRunContext(
        run_id="run_1",
        trigger=AgentTrigger(type="message.received"),
        event=AgentEventContext(
            event_id="evt_1",
            event_type="message.received",
            source="host_adapter",
        ),
        input=AgentInput(text="hello"),
        delivery=DeliveryContext(surface="pipeline"),
        resources=AgentResources(),
        runtime=AgentRuntimeContext(),
        actor=ActorContext(actor_type="user", actor_id="user_1"),
    )

    for plugin_dir, method_name in {
        "coze-agent": "_get_user_id",
        "dify-agent": "_get_user_tag",
        "langflow-agent": "_get_user_tag",
        "n8n-agent": "_get_user_tag",
        "tbox-agent": "_get_user_id",
    }.items():
        module = _load_runner_module(plugin_dir)
        runner = object.__new__(module.DefaultAgentRunner)
        assert getattr(runner, method_name)(ctx) == "user_user_1"


def test_non_streaming_capability_metadata_is_honored_when_supported() -> None:
    from langbot_plugin.api.entities.builtin.agent_runner import (
        AgentEventContext,
        AgentInput,
        AgentResources,
        AgentRunContext,
        AgentRuntimeContext,
        AgentTrigger,
        DeliveryContext,
    )

    for plugin_dir in {"langflow-agent", "tbox-agent"}:
        module = _load_runner_module(plugin_dir)
        runner = object.__new__(module.DefaultAgentRunner)
        ctx = AgentRunContext(
            run_id="run_1",
            trigger=AgentTrigger(type="message.received"),
            event=AgentEventContext(
                event_id="evt_1",
                event_type="message.received",
                source="host_adapter",
            ),
            input=AgentInput(text="hello"),
            delivery=DeliveryContext(surface="pipeline"),
            resources=AgentResources(),
            runtime=AgentRuntimeContext(metadata={"streaming_supported": False}),
            config={},
        )

        assert runner._should_stream(ctx) is False


def _agent_run_context(*, text: str = "hello", config: dict | None = None):
    from langbot_plugin.api.entities.builtin.agent_runner import (
        AgentEventContext,
        AgentInput,
        AgentResources,
        AgentRunContext,
        AgentRunState,
        AgentRuntimeContext,
        AgentTrigger,
        DeliveryContext,
    )
    from langbot_plugin.api.entities.builtin.agent_runner.context_access import (
        ContextAccess,
        ContextAPICapabilities,
    )

    return AgentRunContext(
        run_id="run_1",
        trigger=AgentTrigger(type="message.received"),
        event=AgentEventContext(
            event_id="evt_1",
            event_type="message.received",
            source="host_adapter",
        ),
        input=AgentInput(text=text),
        delivery=DeliveryContext(surface="pipeline"),
        resources=AgentResources.model_validate(
            {
                "knowledge_bases": [{"kb_id": "kb_1"}],
                "tools": [{"tool_name": "weather"}],
            }
        ),
        context=ContextAccess(
            available_apis=ContextAPICapabilities(history_page=True),
        ),
        runtime=AgentRuntimeContext(),
        config=config or {},
        state=AgentRunState(
            conversation={
                "external.session_id": "sess_existing",
                "external.working_directory": "/tmp",
            }
        ),
    )


async def _collect_results(runner, ctx):
    return [item async for item in runner.run(ctx)]


class RecordingRunAPI:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def history_page(self, **kwargs):
        self.calls.append(("history_page", kwargs))
        return {"items": [{"text": "history-from-host"}], "has_more": False}

    async def retrieve_knowledge(self, **kwargs):
        self.calls.append(("retrieve_knowledge", kwargs))
        return [{"content": f"rag:{kwargs['query_text']}"}]

    async def call_tool(self, **kwargs):
        self.calls.append(("call_tool", kwargs))
        return {"ok": True, "tool_name": kwargs["tool_name"], "parameters": kwargs["parameters"]}


def _write_fake_mcp_harness(tmp_path: Path) -> Path:
    script = tmp_path / "fake_mcp_harness.py"
    script.write_text(
        r"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _mcp_call(process, message):
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("MCP proxy returned no response")
    data = json.loads(line)
    if "error" in data:
        raise RuntimeError(data["error"]["message"])
    return data["result"]


stdin = sys.stdin.read()
match = re.search(r"(?:Claude Code|Codex) MCP config: (.+)", stdin)
if not match:
    raise SystemExit("missing MCP config path in runner prompt")

mcp_config = json.loads(Path(match.group(1).strip()).read_text(encoding="utf-8"))
server = mcp_config["mcpServers"]["langbot_agent"]
env = os.environ.copy()
env.update(server.get("env") or {})
process = subprocess.Popen(
    [server["command"], *server.get("args", [])],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    env=env,
)
try:
    _mcp_call(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    tools = _mcp_call(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tool_names = {tool["name"] for tool in tools["tools"]}
    for required in {"langbot_history_page", "langbot_retrieve_knowledge", "langbot_call_tool"}:
        if required not in tool_names:
            raise RuntimeError(f"missing MCP tool: {required}")

    history = _mcp_call(
        process,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "langbot_history_page", "arguments": {"limit": 2}},
        },
    )["structuredContent"]
    rag = _mcp_call(
        process,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "langbot_retrieve_knowledge",
                "arguments": {"kb_id": "kb_1", "query_text": "agent-runner", "top_k": 2},
            },
        },
    )["structuredContent"]["result"]
    tool = _mcp_call(
        process,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "langbot_call_tool",
                "arguments": {"tool_name": "weather", "parameters": {"city": "Shanghai"}},
            },
        },
    )["structuredContent"]
finally:
    assert process.stdin is not None
    process.stdin.close()
    process.wait(timeout=10)

content = (
    "MCP_ACTIONS_OK "
    f"HISTORY={history['items'][0]['text']} "
    f"RAG={rag[0]['content']} "
    f"TOOL={tool['tool_name']}:{tool['parameters']['city']}"
)

if "--output-last-message" in sys.argv:
    output_path = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    print(json.dumps({"type": "thread.started", "thread_id": "thread_mcp_actions"}))
else:
    print(json.dumps({"type": "result", "session_id": "sess_mcp_actions", "result": content}))
""",
        encoding="utf-8",
    )
    return script


def _write_fake_remote_claude_mcp_harness(tmp_path: Path) -> Path:
    script = tmp_path / "claude"
    script.write_text(
        r"""#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _mcp_call(process, message):
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        stderr = process.stderr.read() if process.stderr is not None else ""
        raise RuntimeError(f"MCP shim returned no response: {stderr}")
    data = json.loads(line)
    if "error" in data:
        raise RuntimeError(data["error"]["message"])
    return data["result"]


stdin = sys.stdin.read()
if "--mcp-config" not in sys.argv:
    raise SystemExit("missing --mcp-config")

mcp_config = json.loads(Path(sys.argv[sys.argv.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
server = mcp_config["mcpServers"]["langbot_agent"]
env = os.environ.copy()
env.update(server.get("env") or {})
process = subprocess.Popen(
    [server["command"], *server.get("args", [])],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    env=env,
)
try:
    _mcp_call(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    tools = _mcp_call(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tool_names = {tool["name"] for tool in tools["tools"]}
    for required in {"langbot_history_page", "langbot_retrieve_knowledge", "langbot_call_tool"}:
        if required not in tool_names:
            raise RuntimeError(f"missing MCP tool: {required}")

    history = _mcp_call(
        process,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "langbot_history_page", "arguments": {"limit": 2}},
        },
    )["structuredContent"]
    rag = _mcp_call(
        process,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "langbot_retrieve_knowledge",
                "arguments": {"kb_id": "kb_1", "query_text": "agent-runner", "top_k": 2},
            },
        },
    )["structuredContent"]["result"]
    tool = _mcp_call(
        process,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "langbot_call_tool",
                "arguments": {"tool_name": "weather", "parameters": {"city": "Shanghai"}},
            },
        },
    )["structuredContent"]
finally:
    assert process.stdin is not None
    process.stdin.close()
    process.wait(timeout=10)

content = (
    "REMOTE_MCP_OK "
    f"STDIN={stdin.strip()} "
    f"HISTORY={history['items'][0]['text']} "
    f"RAG={rag[0]['content']} "
    f"TOOL={tool['tool_name']}:{tool['parameters']['city']}"
)
print(json.dumps({"type": "result", "session_id": "sess_remote_mcp", "result": content}))
""",
        encoding="utf-8",
    )
    return script


def _expected_langbot_action_calls() -> list[tuple[str, dict]]:
    return [
        (
            "history_page",
            {
                "conversation_id": None,
                "before_cursor": None,
                "after_cursor": None,
                "limit": 2,
                "direction": "backward",
                "include_artifacts": False,
            },
        ),
        (
            "retrieve_knowledge",
            {
                "kb_id": "kb_1",
                "query_text": "agent-runner",
                "top_k": 2,
                "filters": {},
            },
        ),
        (
            "call_tool",
            {
                "tool_name": "weather",
                "parameters": {"city": "Shanghai"},
            },
        ),
    ]


def test_claude_code_runner_invokes_default_cli(monkeypatch, tmp_path) -> None:
    module = _load_runner_module("claude-code-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    captured = {}
    monkeypatch.setenv("LANGBOT_INTERNAL_TOKEN", "must-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgres://must-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "provider-token")

    class FakeProcess:
        returncode = 0

        async def communicate(self, stdin):
            captured["stdin"] = stdin
            return b'{"type":"result","session_id":"sess_new","result":"assistant output"}\n', b""

        def kill(self):
            captured["killed"] = True

        async def wait(self):
            return None

    async def fake_create_subprocess_exec(*command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    ctx = _agent_run_context(
        text="summarize this",
        config={
            "working-directory": str(tmp_path),
            "model": "sonnet",
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert captured["command"] == (
        "claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        "sonnet",
        "--resume",
        "sess_existing",
    )
    assert b"LangBot prepared read-only run context" in captured["stdin"]
    assert b"summarize this" in captured["stdin"]
    assert captured["kwargs"]["stdin"] is module.asyncio.subprocess.PIPE
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["kwargs"]["env"]["ANTHROPIC_API_KEY"] == "provider-token"
    assert "LANGBOT_INTERNAL_TOKEN" not in captured["kwargs"]["env"]
    assert "DATABASE_URL" not in captured["kwargs"]["env"]
    assert [result.type.value for result in results] == [
        "message.completed",
        "state.updated",
        "state.updated",
        "run.completed",
    ]
    assert results[0].data["message"]["content"] == "assistant output"
    assert results[1].data == {
        "scope": "conversation",
        "key": "external.session_id",
        "value": "sess_new",
    }
    assert results[2].data == {
        "scope": "conversation",
        "key": "external.working_directory",
        "value": str(tmp_path),
    }


def test_claude_code_runner_injects_context(monkeypatch, tmp_path) -> None:
    module = _load_runner_module("claude-code-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, stdin):
            captured["stdin"] = stdin
            return b'{"type":"result","session_id":"sess_new","result":"assistant output"}\n', b""

        def kill(self):
            captured["killed"] = True

        async def wait(self):
            return None

    async def fake_create_subprocess_exec(*command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    ctx = _agent_run_context(
        text="use langbot context",
        config={
            "working-directory": str(tmp_path),
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    run_dir = tmp_path / ".langbot" / "agent-runner" / "run_1"
    context_json = run_dir / "agent-context.json"
    context_markdown = run_dir / "LANGBOT_CONTEXT.md"

    assert captured["command"] == (
        "claude",
        "-p",
        "--output-format",
        "json",
        "--resume",
        "sess_existing",
    )
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert b"LangBot prepared read-only run context" in captured["stdin"]
    assert str(context_json).encode("utf-8") in captured["stdin"]
    assert b"use langbot context" in captured["stdin"]

    assert context_json.exists()
    assert context_markdown.exists()
    context_payload = json.loads(context_json.read_text(encoding="utf-8"))
    assert context_payload["schema"] == "langbot.agent_runner.external_harness_context.v1"
    assert context_payload["event"]["event_type"] == "message.received"
    assert context_payload["input"]["text"] == "use langbot context"
    assert [result.type.value for result in results] == [
        "message.completed",
        "state.updated",
        "state.updated",
        "run.completed",
    ]
    assert results[-1].data["external"]["provider"] == "claude_code"
    assert results[-1].data["external"]["session_id"] == "sess_new"


def test_claude_code_runner_rejects_context_directory_escape(tmp_path) -> None:
    module = _load_runner_module("claude-code-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    ctx = _agent_run_context(
        text="escape context",
        config={
            "working-directory": str(tmp_path),
            "context-directory": "../outside",
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert [result.type.value for result in results] == ["run.failed"]
    assert results[0].data["code"] == "claude_code.context_injection_error"
    assert "context directory" in results[0].data["error"]


def test_claude_code_dangerous_permission_bypass_requires_explicit_config(monkeypatch, tmp_path) -> None:
    module = _load_runner_module("claude-code-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, stdin):
            return b'{"type":"result","session_id":"sess_new","result":"assistant output"}\n', b""

        async def wait(self):
            return None

    async def fake_create_subprocess_exec(*command, **kwargs):
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    ctx = _agent_run_context(
        text="danger mode",
        config={
            "working-directory": str(tmp_path),
            "dangerously-skip-permissions": True,
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert "--dangerously-skip-permissions" in captured["command"]
    assert results[0].data["message"]["content"] == "assistant output"


def test_claude_code_runner_command_not_found_is_structured(monkeypatch, tmp_path) -> None:
    module = _load_runner_module("claude-code-agent")
    runner = object.__new__(module.DefaultAgentRunner)

    async def fake_create_subprocess_exec(*command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    ctx = _agent_run_context(
        text="hello",
        config={
            "working-directory": str(tmp_path),
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert [result.type.value for result in results] == ["run.failed"]
    assert results[0].data["code"] == "claude_code.command_not_found"
    assert "claude" in results[0].data["error"]


def test_claude_code_runner_nonzero_exit_is_structured(monkeypatch, tmp_path) -> None:
    module = _load_runner_module("claude-code-agent")
    runner = object.__new__(module.DefaultAgentRunner)

    class FakeProcess:
        returncode = 2

        async def communicate(self, stdin):
            return b"", b"bad claude config"

        def kill(self):
            raise AssertionError("kill should not be called")

        async def wait(self):
            return None

    async def fake_create_subprocess_exec(*command, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    ctx = _agent_run_context(
        text="hello",
        config={
            "working-directory": str(tmp_path),
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert [result.type.value for result in results] == ["run.failed"]
    assert results[0].data["code"] == "claude_code.cli_error"
    assert results[0].data["error"] == "bad claude config"


def test_claude_code_runner_timeout_is_structured(monkeypatch, tmp_path) -> None:
    module = _load_runner_module("claude-code-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    captured = {}

    class FakeProcess:
        returncode = None

        async def communicate(self, stdin):
            await module.asyncio.sleep(1)
            return b"", b""

        def kill(self):
            captured["killed"] = True

        async def wait(self):
            captured["waited"] = True

    async def fake_create_subprocess_exec(*command, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    ctx = _agent_run_context(
        text="hello",
        config={
            "working-directory": str(tmp_path),
            "timeout": 0.01,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert [result.type.value for result in results] == ["run.failed"]
    assert results[0].data["code"] == "claude_code.timeout"
    assert results[0].data["retryable"] is True
    assert captured == {"killed": True, "waited": True}


def test_claude_code_runner_remote_posts_run_request(monkeypatch) -> None:
    module = _load_runner_module("claude-code-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    captured = {}
    api = RecordingRunAPI()

    async def fake_run_remote_channel(endpoint, token, request_payload, timeout, *, mcp_handler=None):
        captured["endpoint"] = endpoint
        captured["token"] = token
        captured["request"] = request_payload
        captured["timeout"] = timeout
        captured["mcp_result"] = await mcp_handler("tools/list", {}) if mcp_handler else None
        return {
            "ok": True,
            "returncode": 0,
            "stdout": '{"type":"result","session_id":"sess_remote","result":"remote output"}\n',
            "stderr": "",
            "working_directory": "/remote/workspaces/ws-1",
        }

    monkeypatch.setattr(module.remote_client, "run_remote_channel", fake_run_remote_channel)
    monkeypatch.setattr(runner, "get_run_api", lambda ctx: api)
    ctx = _agent_run_context(
        text="run remotely",
        config={
            "execution-mode": "remote",
            "remote-endpoint": "http://remote-daemon:8765",
            "remote-token": "secret-token",
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert captured["endpoint"] == "http://remote-daemon:8765"
    assert captured["token"] == "secret-token"
    assert captured["timeout"] == 45
    request = captured["request"]
    assert request["schema"] == "langbot.claude_code.remote_run.v1"
    assert request["agent"] == "claude_code"
    assert request["run_id"] == "run_1"
    assert request["runtime_id"] == "default"
    assert request["workspace_key"] == "default"
    assert request["resume_session_id"] == "sess_existing"
    assert "LangBot prepared read-only run context" in request["stdin"]
    assert "run remotely" in request["stdin"]
    assert request["config"] == {
        "cli_command": "claude",
        "model": "",
        "output_format": "json",
        "dangerously_skip_permissions": False,
    }

    projected = {item["path"]: item["content"] for item in request["files"]}
    assert ".langbot/agent-runner/run_1/agent-context.json" in projected
    assert ".langbot/agent-runner/run_1/LANGBOT_CONTEXT.md" in projected
    context_payload = json.loads(projected[".langbot/agent-runner/run_1/agent-context.json"])
    assert context_payload["working_directory"] == "default"
    assert {
        "langbot_history_page",
        "langbot_retrieve_knowledge",
        "langbot_call_tool",
    }.issubset({tool["name"] for tool in captured["mcp_result"]["tools"]})

    assert [result.type.value for result in results] == [
        "message.completed",
        "state.updated",
        "state.updated",
        "state.updated",
        "run.completed",
    ]
    assert results[0].data["message"]["content"] == "remote output"
    assert results[1].data == {
        "scope": "conversation",
        "key": "external.session_id",
        "value": "sess_remote",
    }
    assert results[2].data == {
        "scope": "conversation",
        "key": "external.runtime_id",
        "value": "default",
    }
    assert results[3].data == {
        "scope": "conversation",
        "key": "external.workspace_key",
        "value": "default",
    }
    assert results[-1].data["external"]["runtime_id"] == "default"
    assert results[-1].data["external"]["workspace_key"] == "default"


def test_claude_code_remote_daemon_materializes_files_and_uses_command_path(tmp_path) -> None:
    module = _load_claude_code_daemon_module()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_claude = bin_dir / "fake-claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys

stdin = sys.stdin.read()
assert pathlib.Path(".langbot/run/context.txt").read_text(encoding="utf-8") == "context file"
print(json.dumps({"type": "result", "session_id": "daemon_sess", "result": f"daemon:{stdin.strip()}"}))
""",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)

    payload = {
        "schema": "langbot.claude_code.remote_run.v1",
        "workspace_key": "workspace:one",
        "resume_session_id": "",
        "stdin": "hello daemon",
        "timeout": 10,
        "config": {
            "cli_command": "fake-claude",
            "output_format": "json",
        },
        "files": [
            {
                "path": ".langbot/run/context.txt",
                "content": "context file",
                "mode": 0o644,
            }
        ],
    }

    result = asyncio.run(module.handle_run_request(payload, tmp_path / "workspaces", str(bin_dir)))

    assert result["ok"] is True
    assert result["returncode"] == 0
    assert result["working_directory"].endswith("workspace-one")
    assert "daemon:hello daemon" in result["stdout"]
    assert (tmp_path / "workspaces" / "workspace-one" / ".langbot" / "run" / "context.txt").read_text(
        encoding="utf-8"
    ) == "context file"


def test_claude_code_remote_daemon_rejects_unsafe_file_paths(tmp_path) -> None:
    module = _load_claude_code_daemon_module()
    payload = {
        "schema": "langbot.claude_code.remote_run.v1",
        "workspace_key": "workspace",
        "config": {"cli_command": "fake-claude"},
        "files": [{"path": "../escape.txt", "content": "bad"}],
    }

    result = asyncio.run(module.handle_run_request(payload, tmp_path / "workspaces"))

    assert result == {
        "ok": False,
        "code": "invalid_request",
        "error": "invalid relative file path: ../escape.txt",
    }


def test_remote_daemon_rejects_symlink_file_projection_escape(tmp_path) -> None:
    module = _load_shared_daemon_module()
    base_dir = tmp_path / "workspaces"
    workspace_dir = base_dir / "workspace"
    outside_dir = tmp_path / "outside"
    workspace_dir.mkdir(parents=True)
    outside_dir.mkdir()
    (workspace_dir / "link").symlink_to(outside_dir, target_is_directory=True)

    payload = {
        "schema": "langbot.codex.remote_run.v1",
        "workspace_key": "workspace",
        "config": {"cli_command": "fake-codex"},
        "files": [{"path": "link/escape.txt", "content": "bad"}],
    }

    result = asyncio.run(module.handle_run_request(payload, base_dir))

    assert result == {
        "ok": False,
        "code": "invalid_request",
        "error": "invalid relative file path: link/escape.txt",
    }
    assert not (outside_dir / "escape.txt").exists()


def test_claude_code_remote_channel_invokes_langbot_actions(monkeypatch, tmp_path) -> None:
    runner_module = _load_runner_module("claude-code-agent")
    daemon_module = _load_shared_daemon_module()
    runner = object.__new__(runner_module.DefaultAgentRunner)
    api = RecordingRunAPI()
    monkeypatch.setattr(runner, "get_run_api", lambda ctx: api)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_claude = _write_fake_remote_claude_mcp_harness(bin_dir)
    fake_claude.chmod(0o755)

    server = daemon_module.RemoteAgentHTTPServer(
        ("127.0.0.1", 0),
        base_dir=tmp_path / "workspaces",
        token="secret-token",
        command_path=str(bin_dir),
        forced_agent=daemon_module.CLAUDE_CODE_ADAPTER.name,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        ctx = _agent_run_context(
            text="use remote mcp actions",
            config={
                "execution-mode": "remote",
                "remote-endpoint": f"http://{host}:{port}",
                "remote-token": "secret-token",
                "timeout": 20,
            },
        )

        results = asyncio.run(_collect_results(runner, ctx))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert [result.type.value for result in results] == [
        "message.completed",
        "state.updated",
        "state.updated",
        "state.updated",
        "run.completed",
    ]
    content = results[0].data["message"]["content"]
    assert "REMOTE_MCP_OK" in content
    assert "use remote mcp actions" in content
    assert "HISTORY=history-from-host" in content
    assert "RAG=rag:agent-runner" in content
    assert "TOOL=weather:Shanghai" in content
    assert results[1].data["value"] == "sess_remote_mcp"
    assert api.calls == _expected_langbot_action_calls()


def test_codex_remote_daemon_materializes_files_and_uses_command_path(monkeypatch, tmp_path) -> None:
    module = _load_codex_daemon_module()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_codex = bin_dir / "fake-codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "stdin = sys.stdin.read()\n"
        "assert pathlib.Path('ctx/info.txt').read_text(encoding='utf-8') == 'remote context'\n"
        "assert str(pathlib.Path.cwd()) in os.getcwd()\n"
        "assert stdin == 'hello remote codex'\n"
        "assert 'LANGBOT_INTERNAL_TOKEN' not in os.environ\n"
        "assert 'DATABASE_URL' not in os.environ\n"
        "assert os.environ.get('OPENAI_API_KEY') == 'provider-token'\n"
        "print(json.dumps({'type':'thread.started','thread_id':'thread_daemon'}))\n"
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'daemon codex ok'}}))\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    monkeypatch.setenv("LANGBOT_INTERNAL_TOKEN", "must-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgres://must-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-token")
    payload = {
        "schema": "langbot.codex.remote_run.v1",
        "workspace_key": "workspace-a",
        "resume_session_id": "",
        "stdin": "hello remote codex",
        "timeout": 10,
        "config": {
            "cli_command": "fake-codex",
            "output_format": "json",
            "resume": False,
            "skip_git_repo_check": True,
            "approval_policy": "never",
        },
        "files": [{"path": "ctx/info.txt", "content": "remote context"}],
    }

    result = asyncio.run(module.handle_run_request(payload, tmp_path / "workspaces", str(bin_dir)))

    assert result["ok"] is True
    assert result["returncode"] == 0
    assert '"thread_daemon"' in result["stdout"]
    assert "daemon codex ok" in result["stdout"]
    assert Path(result["working_directory"]).name == "workspace-a"


def test_codex_remote_daemon_rejects_unsafe_file_paths(tmp_path) -> None:
    module = _load_codex_daemon_module()
    payload = {
        "schema": "langbot.codex.remote_run.v1",
        "workspace_key": "workspace",
        "config": {"cli_command": "fake-codex"},
        "files": [{"path": "../escape.txt", "content": "bad"}],
    }

    result = asyncio.run(module.handle_run_request(payload, tmp_path / "workspaces"))

    assert result == {
        "ok": False,
        "code": "invalid_request",
        "error": "invalid relative file path: ../escape.txt",
    }


def test_codex_remote_daemon_writes_mcp_config_to_codex_home_without_argv_secret(tmp_path) -> None:
    module = _load_shared_daemon_module()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record_path = tmp_path / "record.json"
    shared_codex_home = tmp_path / "shared-codex-home"
    shared_codex_home.mkdir()
    (shared_codex_home / "auth.json").write_text('{"token":"shared-auth"}', encoding="utf-8")
    (shared_codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "custom"',
                "",
                "[model_providers.custom]",
                'base_url = "https://example.invalid/v1"',
                "",
                "[mcp_servers.global_leak]",
                'command = "global-mcp"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    fake_codex = bin_dir / "fake-codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        f"record = pathlib.Path({str(record_path)!r})\n"
        "codex_home = pathlib.Path(os.environ['CODEX_HOME'])\n"
        "config_text = (codex_home / 'config.toml').read_text(encoding='utf-8')\n"
        "assert (codex_home / 'auth.json').exists()\n"
        "record.write_text(json.dumps({'argv': sys.argv, 'codex_home': str(codex_home), 'config': config_text}), encoding='utf-8')\n"
        "assert 'remote-secret' not in ' '.join(sys.argv)\n"
        "assert 'LANGBOT_REMOTE_MCP_SECRET' in config_text\n"
        "assert 'remote-secret' in config_text\n"
        "assert 'model_provider = \"custom\"' in config_text\n"
        "assert 'global_leak' not in config_text\n"
        "print(json.dumps({'type':'thread.started','thread_id':'thread_remote_mcp'}))\n"
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'remote codex mcp ok'}}))\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    payload = {
        "schema": "langbot.codex.remote_run.v1",
        "run_id": "run-secret",
        "workspace_key": "workspace-mcp",
        "stdin": "hello remote codex",
        "timeout": 10,
        "config": {
            "cli_command": "fake-codex",
            "output_format": "json",
            "resume": False,
            "skip_git_repo_check": True,
            "approval_policy": "never",
            "config_overrides": ['mcp_servers.evil.env.TOKEN="argv-leak"'],
        },
    }
    run_channel = module.ActiveRunChannel(
        run_id="run-secret",
        secret="remote-secret",
        pending_requests={},
        outgoing=asyncio.Queue(),
    )
    old_codex_home = os.environ.get("CODEX_HOME")
    os.environ["CODEX_HOME"] = str(shared_codex_home)

    try:
        result = asyncio.run(
            module.execute_run_payload(
                payload,
                module.CODEX_ADAPTER,
                tmp_path / "workspaces",
                str(bin_dir),
                run_channel=run_channel,
                daemon_endpoint="http://127.0.0.1:8766",
            )
        )
    finally:
        if old_codex_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = old_codex_home

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert "remote codex mcp ok" in result["stdout"]
    assert "remote-secret" not in " ".join(record["argv"])
    assert "argv-leak" not in " ".join(record["argv"])
    assert record["codex_home"].endswith(".langbot/agent-runner/run-secret/codex-home")
    assert "global_leak" not in record["config"]
    assert "remote-secret" in record["config"]


def test_shared_remote_daemon_routes_agent_adapters(tmp_path) -> None:
    module = _load_shared_daemon_module()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_codex = bin_dir / "fake-codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "stdin = sys.stdin.read()\n"
        "assert '--json' in sys.argv\n"
        "assert '--skip-git-repo-check' in sys.argv\n"
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'codex:' + stdin}}))\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    payload = {
        "schema": "langbot.remote_agent.run.v1",
        "agent": "codex",
        "workspace_key": "shared-workspace",
        "stdin": "shared daemon",
        "timeout": 10,
        "config": {
            "cli_command": "fake-codex",
            "output_format": "json",
            "resume": False,
            "skip_git_repo_check": True,
            "approval_policy": "never",
        },
        "files": [{"path": "ctx/info.txt", "content": "shared context"}],
    }

    result = asyncio.run(module.handle_run_request(payload, tmp_path / "workspaces", str(bin_dir)))

    assert result["ok"] is True
    assert "codex:shared daemon" in result["stdout"]
    assert Path(result["working_directory"]).name == "shared-workspace"


def test_codex_runner_invokes_configured_cli(monkeypatch, tmp_path) -> None:
    module = _load_runner_module("codex-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, stdin):
            captured["stdin"] = stdin
            output_path = Path(captured["command"][captured["command"].index("--output-last-message") + 1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("assistant output", encoding="utf-8")
            return (
                b'{"type":"thread.started","thread_id":"thread_new"}\n{"type":"turn.completed","usage":{"input_tokens":1}}\n',
                b"",
            )

        def kill(self):
            captured["killed"] = True

        async def wait(self):
            return None

    async def fake_create_subprocess_exec(*command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    ctx = _agent_run_context(
        text="summarize this",
        config={
            "cli-command": "codex",
            "working-directory": str(tmp_path),
            "inject-context": False,
            "model": "gpt-5.5",
            "resume": False,
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    last_message_path = tmp_path / ".langbot" / "agent-runner" / "run_1" / "codex-last-message.txt"
    assert captured["command"] == (
        "codex",
        "exec",
        "--json",
        "--output-last-message",
        str(last_message_path),
        "--model",
        "gpt-5.5",
        "--sandbox",
        "read-only",
        "--cd",
        str(tmp_path),
        "--skip-git-repo-check",
        "--config",
        'approval_policy="never"',
        "-",
    )
    assert captured["stdin"] == b"summarize this"
    assert captured["kwargs"]["stdin"] is module.asyncio.subprocess.PIPE
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert [result.type.value for result in results] == [
        "message.completed",
        "state.updated",
        "state.updated",
        "run.completed",
    ]
    assert results[0].data["message"]["content"] == "assistant output"
    assert results[1].data == {
        "scope": "conversation",
        "key": "external.session_id",
        "value": "thread_new",
    }
    assert results[2].data == {
        "scope": "conversation",
        "key": "external.working_directory",
        "value": str(tmp_path),
    }
    assert results[-1].data["external"]["provider"] == "codex"


def test_codex_runner_remote_posts_run_request(monkeypatch) -> None:
    module = _load_runner_module("codex-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    captured = {}

    def fake_post_remote_run(endpoint, token, request_payload, timeout):
        captured["endpoint"] = endpoint
        captured["token"] = token
        captured["request"] = request_payload
        captured["timeout"] = timeout
        return {
            "ok": True,
            "returncode": 0,
            "stdout": (
                '{"type":"thread.started","thread_id":"thread_remote"}\n'
                '{"type":"item.completed","item":{"type":"agent_message","text":"remote codex output"}}\n'
            ),
            "stderr": "",
            "working_directory": "/remote/workspaces/ws-1",
        }

    monkeypatch.setattr(module, "_post_remote_run", fake_post_remote_run)
    ctx = _agent_run_context(
        text="run codex remotely",
        config={
            "execution-mode": "remote",
            "remote-endpoint": "http://remote-codex:8766",
            "remote-token": "secret-token",
            "remote-runtime-id": "runtime-codex",
            "remote-workspace-key": "workspace-codex",
            "remote-request-timeout": 42,
            "context-directory": ".langbot/remote-codex",
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert captured["endpoint"] == "http://remote-codex:8766"
    assert captured["token"] == "secret-token"
    assert captured["timeout"] == 42
    request = captured["request"]
    assert request["schema"] == "langbot.codex.remote_run.v1"
    assert request["run_id"] == "run_1"
    assert request["runtime_id"] == "runtime-codex"
    assert request["workspace_key"] == "workspace-codex"
    assert request["resume_session_id"] == "sess_existing"
    assert "LangBot prepared read-only run context" in request["stdin"]
    assert "run codex remotely" in request["stdin"]
    assert request["config"]["cli_command"] == "codex"
    assert request["config"]["output_format"] == "json"
    assert "mcp_config_path" not in request

    projected = {item["path"]: item["content"] for item in request["files"]}
    assert ".langbot/remote-codex/run_1/agent-context.json" in projected
    assert ".langbot/remote-codex/run_1/LANGBOT_CONTEXT.md" in projected
    assert not any(path.endswith("/mcp.json") for path in projected)
    assert not any("/codex-skills/" in path for path in projected)
    context_payload = module.json.loads(projected[".langbot/remote-codex/run_1/agent-context.json"])
    assert context_payload["working_directory"] == "workspace-codex"

    assert [result.type.value for result in results] == [
        "message.completed",
        "state.updated",
        "state.updated",
        "state.updated",
        "run.completed",
    ]
    assert results[0].data["message"]["content"] == "remote codex output"
    assert results[1].data == {
        "scope": "conversation",
        "key": "external.session_id",
        "value": "thread_remote",
    }
    assert results[2].data == {
        "scope": "conversation",
        "key": "external.runtime_id",
        "value": "runtime-codex",
    }
    assert results[3].data == {
        "scope": "conversation",
        "key": "external.workspace_key",
        "value": "workspace-codex",
    }
    assert results[-1].data["external"]["runtime_id"] == "runtime-codex"
    assert results[-1].data["external"]["workspace_key"] == "workspace-codex"

def test_codex_runner_resumes_existing_thread(monkeypatch, tmp_path) -> None:
    module = _load_runner_module("codex-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, stdin):
            captured["stdin"] = stdin
            output_path = Path(captured["command"][captured["command"].index("--output-last-message") + 1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("resumed output", encoding="utf-8")
            return b'{"type":"thread.started","thread_id":"thread_resumed"}\n', b""

        def kill(self):
            captured["killed"] = True

        async def wait(self):
            return None

    async def fake_create_subprocess_exec(*command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    ctx = _agent_run_context(
        text="continue",
        config={
            "working-directory": str(tmp_path),
            "inject-context": False,
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    last_message_path = tmp_path / ".langbot" / "agent-runner" / "run_1" / "codex-last-message.txt"
    assert captured["command"] == (
        "codex",
        "exec",
        "resume",
        "--json",
        "--output-last-message",
        str(last_message_path),
        "--skip-git-repo-check",
        "--config",
        'approval_policy="never"',
        "sess_existing",
        "-",
    )
    assert captured["stdin"] == b"continue"
    assert results[0].data["message"]["content"] == "resumed output"
    assert results[1].data["value"] == "thread_resumed"


def test_codex_runner_injects_context(monkeypatch, tmp_path) -> None:
    module = _load_runner_module("codex-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    captured = {}
    monkeypatch.setenv("LANGBOT_INTERNAL_TOKEN", "must-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgres://must-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-token")

    class FakeProcess:
        returncode = 0

        async def communicate(self, stdin):
            captured["stdin"] = stdin
            output_path = Path(captured["command"][captured["command"].index("--output-last-message") + 1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("assistant output", encoding="utf-8")
            return b'{"type":"thread.started","thread_id":"thread_new"}\n', b""

        def kill(self):
            captured["killed"] = True

        async def wait(self):
            return None

    async def fake_create_subprocess_exec(*command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    ctx = _agent_run_context(
        text="use langbot context",
        config={
            "cli-command": "codex",
            "working-directory": str(tmp_path),
            "context-directory": ".langbot/test-agent-runner",
            "resume": False,
            "environment-json": {"HTTP_PROXY": "http://127.0.0.1:7890"},
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    run_dir = tmp_path / ".langbot" / "test-agent-runner" / "run_1"
    context_json = run_dir / "agent-context.json"
    context_markdown = run_dir / "LANGBOT_CONTEXT.md"

    assert "--config" in captured["command"]
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["kwargs"]["env"]["OPENAI_API_KEY"] == "provider-token"
    assert captured["kwargs"]["env"]["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert "LANGBOT_INTERNAL_TOKEN" not in captured["kwargs"]["env"]
    assert "DATABASE_URL" not in captured["kwargs"]["env"]
    assert b"LangBot prepared read-only run context" in captured["stdin"]
    assert str(context_json).encode("utf-8") in captured["stdin"]
    assert b"use langbot context" in captured["stdin"]

    assert context_json.exists()
    assert context_markdown.exists()
    assert (run_dir / "codex-events.jsonl").read_text(
        encoding="utf-8"
    ) == '{"type":"thread.started","thread_id":"thread_new"}\n'
    assert not (run_dir / "mcp.json").exists()
    assert not (run_dir / "codex-skills").exists()

    context_payload = module.json.loads(context_json.read_text(encoding="utf-8"))
    assert context_payload["schema"] == "langbot.agent_runner.external_harness_context.v1"
    assert context_payload["event"]["event_type"] == "message.received"
    assert context_payload["input"]["text"] == "use langbot context"
    assert "bootstrap" not in context_payload
    assert [result.type.value for result in results] == [
        "message.completed",
        "state.updated",
        "state.updated",
        "run.completed",
    ]
    assert results[-1].data["external"]["provider"] == "codex"


def test_codex_runner_rejects_protected_environment_override(monkeypatch, tmp_path) -> None:
    module = _load_runner_module("codex-agent")
    runner = object.__new__(module.DefaultAgentRunner)

    async def fake_create_subprocess_exec(*command, **kwargs):
        raise AssertionError("subprocess must not be spawned when env overrides protected keys")

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    ctx = _agent_run_context(
        text="bad env",
        config={
            "working-directory": str(tmp_path),
            "inject-context": False,
            "environment-json": {"CODEX_HOME": "/tmp/evil"},
            "resume": False,
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert [result.type.value for result in results] == ["run.failed"]
    assert results[0].data["code"] == "codex.unexpected_error"
    assert "protected environment variable: CODEX_HOME" in results[0].data["error"]


def test_codex_runner_rejects_context_directory_escape(tmp_path) -> None:
    module = _load_runner_module("codex-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    ctx = _agent_run_context(
        text="escape context",
        config={
            "working-directory": str(tmp_path),
            "context-directory": "/tmp/outside",
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert [result.type.value for result in results] == ["run.failed"]
    assert results[0].data["code"] == "codex.context_injection_error"
    assert "context directory" in results[0].data["error"]


def test_codex_runner_uses_shared_langbot_mcp_bridge(monkeypatch, tmp_path) -> None:
    module = _load_runner_module("codex-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    captured = {}
    shared_codex_home = tmp_path / "shared-codex-home"
    shared_codex_home.mkdir()
    (shared_codex_home / "auth.json").write_text('{"token":"shared-auth"}', encoding="utf-8")
    (shared_codex_home / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "custom"',
                "",
                "[model_providers.custom]",
                'base_url = "https://example.invalid/v1"',
                "",
                "[mcp_servers.global_leak]",
                'command = "global-mcp"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(shared_codex_home))

    class FakeBridge:
        started = False
        stopped = False

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def mcp_server_config(self):
            return {
                "command": "python",
                "args": ["-m", "fake_langbot_mcp"],
                "env": {"TOKEN": "run-token"},
            }

    bridge = FakeBridge()
    monkeypatch.setattr(runner, "create_external_mcp_bridge", lambda ctx: bridge)

    class FakeProcess:
        returncode = 0

        async def communicate(self, stdin):
            captured["stdin"] = stdin
            output_path = Path(captured["command"][captured["command"].index("--output-last-message") + 1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("assistant output", encoding="utf-8")
            return b'{"type":"thread.started","thread_id":"thread_new"}\n', b""

        def kill(self):
            captured["killed"] = True

        async def wait(self):
            return None

    async def fake_create_subprocess_exec(*command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    ctx = _agent_run_context(
        text="use langbot mcp",
        config={
            "working-directory": str(tmp_path),
            "resume": False,
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    run_dir = tmp_path / ".langbot" / "agent-runner" / "run_1"
    mcp_config = run_dir / "mcp.json"
    mcp_data = module.json.loads(mcp_config.read_text(encoding="utf-8"))

    assert bridge.started is True
    assert bridge.stopped is True
    assert "--config" in captured["command"]
    assert not any("mcp_servers.langbot_agent" in arg for arg in captured["command"])
    assert not any("run-token" in arg for arg in captured["command"])
    assert captured["kwargs"]["env"]["CODEX_HOME"] == str(run_dir / "codex-home")
    codex_config = run_dir / "codex-home" / "config.toml"
    codex_config_text = codex_config.read_text(encoding="utf-8")
    assert (run_dir / "codex-home" / "auth.json").exists()
    assert 'model_provider = "custom"' in codex_config_text
    assert "global_leak" not in codex_config_text
    assert "[mcp_servers.langbot_agent]" in codex_config_text
    assert 'TOKEN = "run-token"' in codex_config_text
    assert oct(codex_config.stat().st_mode & 0o777) == "0o600"
    assert mcp_data["mcpServers"]["langbot_agent"]["command"] == "python"
    assert mcp_data["mcpServers"]["langbot_agent"]["tools"]["langbot_call_tool"]["approval_mode"] == "approve"
    assert b"LangBot MCP server: langbot_agent" in captured["stdin"]
    assert [result.type.value for result in results][:1] == ["message.completed"]


def test_codex_runner_external_mcp_bridge_invokes_langbot_actions(monkeypatch, tmp_path) -> None:
    module = _load_runner_module("codex-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    api = RecordingRunAPI()
    monkeypatch.setattr(runner, "get_run_api", lambda ctx: api)

    harness = _write_fake_mcp_harness(tmp_path)
    ctx = _agent_run_context(
        text="use langbot mcp actions",
        config={
            "cli-command": f"{sys.executable} {harness}",
            "working-directory": str(tmp_path),
            "inject-context": False,
            "resume": False,
            "timeout": 20,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert [result.type.value for result in results] == [
        "message.completed",
        "state.updated",
        "state.updated",
        "run.completed",
    ]
    content = results[0].data["message"]["content"]
    assert "MCP_ACTIONS_OK" in content
    assert "HISTORY=history-from-host" in content
    assert "RAG=rag:agent-runner" in content
    assert "TOOL=weather:Shanghai" in content
    assert results[1].data["value"] == "thread_mcp_actions"
    assert api.calls == _expected_langbot_action_calls()


def test_codex_runner_command_not_found_is_structured(monkeypatch, tmp_path) -> None:
    module = _load_runner_module("codex-agent")
    runner = object.__new__(module.DefaultAgentRunner)

    async def fake_create_subprocess_exec(*command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    ctx = _agent_run_context(
        text="hello",
        config={
            "cli-command": "missing-codex",
            "working-directory": str(tmp_path),
            "inject-context": False,
            "resume": False,
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert [result.type.value for result in results] == ["run.failed"]
    assert results[0].data["code"] == "codex.command_not_found"
    assert "missing-codex" in results[0].data["error"]
