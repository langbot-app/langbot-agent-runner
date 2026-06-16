from __future__ import annotations

import asyncio
import importlib.util
import sys
import tomllib
import types
from pathlib import Path

import yaml
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentEventContext,
    AgentInput,
    AgentResources,
    AgentRunContext,
    AgentRuntimeContext,
    AgentTrigger,
    DeliveryContext,
)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIRS = {
    "acp-agent-runner",
    "claude-code-agent",
    "codex-agent",
    "coze-agent",
    "dashscope-agent",
    "deerflow-agent",
    "dify-agent",
    "langflow-agent",
    "n8n-agent",
    "tbox-agent",
    "weknora-agent",
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


def test_repository_builds_as_plugin_collection_not_import_package() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel_target = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]

    assert not (ROOT / "langbot_agent_runner").exists()
    assert set(wheel_target["only-include"]) == PLUGIN_DIRS | {"docs"}


def test_bridge_runners_declare_bridge_related_capabilities() -> None:
    acp_runner = _load_yaml(ROOT / "acp-agent-runner" / "components" / "agent_runner" / "default.yaml")
    assert acp_runner["spec"]["permissions"] == {
        "tools": ["detail", "call"],
        "knowledge_bases": ["retrieve"],
        "history": ["page"],
        "storage": ["plugin"],
    }
    assert acp_runner["spec"]["capabilities"]["tool_calling"] is True
    assert acp_runner["spec"]["capabilities"]["knowledge_retrieval"] is True

    dify_runner = _load_yaml(ROOT / "dify-agent" / "components" / "agent_runner" / "default.yaml")
    assert dify_runner["spec"]["permissions"] == {
        "tools": ["detail", "call"],
        "knowledge_bases": ["retrieve"],
        "history": ["page"],
        "storage": ["plugin"],
    }
    assert dify_runner["spec"]["capabilities"]["tool_calling"] is True
    assert dify_runner["spec"]["capabilities"]["knowledge_retrieval"] is True


def test_acp_provider_presets_match_runner_config() -> None:
    module = _load_runner_module("acp-agent-runner")
    acp_runner = _load_yaml(ROOT / "acp-agent-runner" / "components" / "agent_runner" / "default.yaml")
    provider_config = next(item for item in acp_runner["spec"]["config"] if item["name"] == "provider")
    option_names = {option["name"] for option in provider_config["options"]}

    assert option_names == set(module.DEFAULT_PROVIDER_COMMANDS) | {"custom"}
    assert module.DEFAULT_PROVIDER_COMMANDS["codex"] == "npx -y @zed-industries/codex-acp"
    assert module.DEFAULT_PROVIDER_COMMANDS["qwen-code"] == "npx -y @qwen-code/qwen-code --acp --experimental-skills"
    assert module.DEFAULT_PROVIDER_COMMANDS["opencode"] == "opencode acp"
    unsupported = {
        "agoragentic",
        "cline",
        "cursor",
        "deepcode",
        "deepseek",
        "github-copilot",
        "goose",
        "kimi",
        "langcli",
        "nova",
        "qoder",
        "sigit",
    }
    assert unsupported.isdisjoint(option_names)


def test_acp_runner_uses_sdk_mcp_bridge_helper(monkeypatch) -> None:
    module = _load_runner_module("acp-agent-runner")
    calls = {}

    class FakeBridge:
        server_name = "langbot_agent"
        endpoint = "http://127.0.0.1:12345"
        http_mcp_endpoint = "http://127.0.0.1:12345/mcp/http"

        @classmethod
        def from_run_api(cls, api, ctx, *, host, port, request_timeout):
            calls["api"] = api
            calls["ctx"] = ctx
            calls["host"] = host
            calls["port"] = port
            calls["request_timeout"] = request_timeout
            return cls()

        def start(self):
            calls["started"] = True

        def mcp_server_config(self):
            return {
                "command": "python",
                "args": ["-m", "langbot_plugin.api.agent_tools.mcp_stdio"],
                "env": {"LANGBOT_AGENT_MCP_ENDPOINT": self.endpoint},
            }

    runner = object.__new__(module.DefaultAgentRunner)
    runner.get_run_api = lambda ctx: "run-api"
    ctx = object()

    monkeypatch.setattr(module, "AgentRunMCPBridge", FakeBridge)
    bridge, servers = runner._mcp_servers(
        ctx,
        {
            "mcp_servers": [],
            "mcp_bridge_enabled": True,
            "mcp_bridge_transport": "stdio",
            "mcp_bridge_host": "127.0.0.1",
            "mcp_bridge_port": 0,
            "mcp_bridge_request_timeout": 15.0,
            "mcp_public_url": "",
            "location": "local",
            "langbot_assets_mode": "ephemeral",
        },
    )

    assert isinstance(bridge, FakeBridge)
    assert calls == {
        "api": "run-api",
        "ctx": ctx,
        "host": "127.0.0.1",
        "port": 0,
        "request_timeout": 15.0,
        "started": True,
    }
    assert servers == [
        {
            "name": "langbot_agent",
            "type": "stdio",
            "command": "python",
            "args": ["-m", "langbot_plugin.api.agent_tools.mcp_stdio"],
            "env": [{"name": "LANGBOT_AGENT_MCP_ENDPOINT", "value": "http://127.0.0.1:12345"}],
        }
    ]


def test_acp_runner_can_use_sdk_asset_gateway(monkeypatch) -> None:
    module = _load_runner_module("acp-agent-runner")
    calls = {}

    class FakeRegistration:
        server_name = "langbot_agent"
        endpoint = "http://127.0.0.1:23456"
        http_mcp_endpoint = "http://127.0.0.1:23456/mcp"

        def http_mcp_server_config(self, *, public_url=None):
            calls["public_url"] = public_url
            return {
                "name": self.server_name,
                "url": public_url or self.http_mcp_endpoint,
                "headers": {"Authorization": "Bearer token_1"},
            }

    class FakeGateway:
        def register_run(self, api, ctx, *, ttl_seconds):
            calls["api"] = api
            calls["ctx"] = ctx
            calls["ttl_seconds"] = ttl_seconds
            return FakeRegistration()

    def fake_default_gateway(*, host, port, request_timeout):
        calls["host"] = host
        calls["port"] = port
        calls["request_timeout"] = request_timeout
        return FakeGateway()

    runner = object.__new__(module.DefaultAgentRunner)
    runner.get_run_api = lambda ctx: "run-api"
    ctx = object()

    monkeypatch.setattr(module, "get_default_agent_asset_gateway", fake_default_gateway)
    registration, servers = runner._mcp_servers(
        ctx,
        {
            "mcp_servers": [],
            "mcp_bridge_enabled": True,
            "mcp_bridge_transport": "auto",
            "mcp_bridge_host": "127.0.0.1",
            "mcp_bridge_port": 0,
            "mcp_bridge_request_timeout": 15.0,
            "mcp_public_url": "",
            "location": "local",
            "langbot_assets_mode": "gateway",
            "asset_gateway_host": "127.0.0.1",
            "asset_gateway_port": 8765,
            "asset_gateway_request_timeout": 12.0,
            "asset_gateway_token_ttl": 120.0,
            "asset_gateway_public_url": "http://gateway.example/mcp",
        },
    )

    assert isinstance(registration, FakeRegistration)
    assert calls == {
        "host": "127.0.0.1",
        "port": 8765,
        "request_timeout": 12.0,
        "api": "run-api",
        "ctx": ctx,
        "ttl_seconds": 120.0,
        "public_url": "http://gateway.example/mcp",
    }
    assert servers == [
        {
            "name": "langbot_agent",
            "type": "http",
            "url": "http://gateway.example/mcp",
            "headers": [{"name": "Authorization", "value": "Bearer token_1"}],
        }
    ]


def _native_ctx(config: dict) -> AgentRunContext:
    return AgentRunContext(
        run_id="run_native",
        trigger=AgentTrigger(type="message.received"),
        event=AgentEventContext(event_id="evt_1", event_type="message.received", source="test"),
        input=AgentInput(text="hello native"),
        delivery=DeliveryContext(surface="test"),
        resources=AgentResources(),
        runtime=AgentRuntimeContext(),
        config=config,
    )


def _write_fake_native_cli(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import json",
                "import sys",
                "session_id = 'missing-session'",
                "if '--session-id' in sys.argv:",
                "    session_id = sys.argv[sys.argv.index('--session-id') + 1]",
                "prompt = sys.argv[-1]",
                "print(json.dumps({'type': 'session.started', 'session_id': session_id}))",
                "print(json.dumps({'type': 'message.completed', 'text': 'FAKE_NATIVE_OK:' + prompt}))",
            ]
        ),
        encoding="utf-8",
    )


def _write_fake_codex_app_server(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import json",
                "import sys",
                "thread_id = 'thread-123'",
                "def send(payload):",
                "    print(json.dumps(payload), flush=True)",
                "for line in sys.stdin:",
                "    request = json.loads(line)",
                "    method = request.get('method')",
                "    request_id = request.get('id')",
                "    params = request.get('params') or {}",
                "    if request_id is not None and method == 'initialize':",
                "        send({'jsonrpc': '2.0', 'id': request_id, 'result': {}})",
                "    elif method == 'initialized':",
                "        pass",
                "    elif request_id is not None and method == 'thread/resume':",
                "        thread_id = params.get('threadId') or thread_id",
                "        send({'jsonrpc': '2.0', 'id': request_id, 'result': {'threadId': thread_id}})",
                "    elif request_id is not None and method == 'thread/start':",
                "        send({'jsonrpc': '2.0', 'id': request_id, 'result': {'threadId': thread_id}})",
                "    elif request_id is not None and method == 'turn/start':",
                "        prompt = params.get('input', [{}])[0].get('text', '')",
                "        send({'jsonrpc': '2.0', 'id': request_id, 'result': {}})",
                "        send({'jsonrpc': '2.0', 'method': 'turn/started', 'params': {'threadId': thread_id, 'turn': {'id': 'turn-1'}}})",
                "        send({'jsonrpc': '2.0', 'method': 'item/completed', 'params': {'threadId': thread_id, 'item': {'id': 'item-1', 'type': 'agentMessage', 'text': 'FAKE_CODEX_APP_SERVER_OK:' + prompt, 'phase': 'final_answer'}}})",
                "        send({'jsonrpc': '2.0', 'method': 'item/completed', 'params': {'threadId': thread_id, 'item': {'id': 'item-1', 'type': 'agentMessage', 'text': 'FAKE_CODEX_APP_SERVER_OK:' + prompt, 'phase': 'final_answer'}}})",
                "        send({'jsonrpc': '2.0', 'method': 'item/completed', 'params': {'threadId': thread_id, 'item': {'id': 'item-2', 'type': 'agentMessage', 'text': 'FAKE_CODEX_APP_SERVER_OK:' + prompt, 'phase': 'final_answer'}}})",
                "        send({'jsonrpc': '2.0', 'method': 'turn/completed', 'params': {'threadId': thread_id, 'turn': {'id': 'turn-1', 'status': 'completed'}}})",
            ]
        ),
        encoding="utf-8",
    )


def test_claude_code_runner_executes_fake_native_cli(tmp_path: Path) -> None:
    fake_cli = tmp_path / "fake_native_cli.py"
    _write_fake_native_cli(fake_cli)
    module = _load_runner_module("claude-code-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    runner.get_plugin_config = lambda: {}
    runner.get_run_api = lambda ctx: None

    ctx = _native_ctx(
        {
            "location": "local",
            "command": sys.executable,
            "args-json": [str(fake_cli)],
            "workspace": str(tmp_path),
            "langbot-assets-enabled": False,
        }
    )
    results = asyncio.run(_collect_async(runner.run(ctx)))

    assert [item.type for item in results] == [
        "state.updated",
        "message.delta",
        "message.completed",
        "run.completed",
    ]
    assert results[2].data["message"]["content"] == "FAKE_NATIVE_OK:hello native"
    assert results[0].data["key"] == "external.claude_code_session_id"


def test_codex_runner_executes_fake_native_cli(tmp_path: Path) -> None:
    fake_cli = tmp_path / "fake_codex_app_server.py"
    _write_fake_codex_app_server(fake_cli)
    module = _load_runner_module("codex-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    runner.get_plugin_config = lambda: {}
    runner.get_run_api = lambda ctx: None

    ctx = _native_ctx(
        {
            "location": "local",
            "command": f"{sys.executable} {fake_cli}",
            "workspace": str(tmp_path),
            "langbot-assets-enabled": False,
        }
    )
    results = asyncio.run(_collect_async(runner.run(ctx)))

    assert [item.type for item in results] == [
        "state.updated",
        "message.delta",
        "run.completed",
    ]
    assert results[1].data["chunk"]["content"] == "FAKE_CODEX_APP_SERVER_OK:hello native"
    assert results[1].data["chunk"]["is_final"] is True
    assert results[0].data["key"] == "external.codex_session_id"
    assert results[0].data["value"] == "thread-123"
    assert [item.sequence for item in results] == [1, 2, 3]


async def _collect_async(stream):
    return [item async for item in stream]


def test_dify_runner_injects_langbot_asset_run_token(monkeypatch) -> None:
    module = _load_runner_module("dify-agent")
    calls = {}

    class FakeRegistration:
        token = "token_1"

        def stop(self):
            calls["stopped"] = True

    class FakeGateway:
        def register_run(self, api, ctx, *, ttl_seconds):
            calls["api"] = api
            calls["ctx"] = ctx
            calls["ttl_seconds"] = ttl_seconds
            return FakeRegistration()

    def fake_default_gateway(*, host, port, request_timeout):
        calls["host"] = host
        calls["port"] = port
        calls["request_timeout"] = request_timeout
        return FakeGateway()

    runner = object.__new__(module.DefaultAgentRunner)
    runner.get_run_api = lambda ctx: "run-api"
    ctx = types.SimpleNamespace(adapter=types.SimpleNamespace(extra={"params": {"existing": "value"}}))

    monkeypatch.setattr(module, "get_default_agent_asset_gateway", fake_default_gateway)
    registration, inputs = runner._prepare_dify_inputs(
        ctx,
        {
            "langbot_assets_enabled": True,
            "asset_gateway_host": "0.0.0.0",
            "asset_gateway_port": 8765,
            "asset_gateway_request_timeout": 12.0,
            "asset_gateway_token_ttl": 120.0,
            "asset_gateway_input_name": "langbot_asset_run_token",
        },
    )

    assert isinstance(registration, FakeRegistration)
    assert inputs == {"existing": "value", "langbot_asset_run_token": "token_1"}
    assert ctx.adapter.extra == {"params": {"existing": "value"}}
    assert calls == {
        "host": "0.0.0.0",
        "port": 8765,
        "request_timeout": 12.0,
        "api": "run-api",
        "ctx": ctx,
        "ttl_seconds": 120.0,
    }

    registration.stop()
    assert calls["stopped"] is True


def test_dify_runner_only_reuses_dify_uuid_conversation_id() -> None:
    module = _load_runner_module("dify-agent")
    runner = object.__new__(module.DefaultAgentRunner)

    ctx = types.SimpleNamespace(
        state=types.SimpleNamespace(
            conversation={
                "external.conversation_id": "550e8400-e29b-41d4-a716-446655440000",
            },
        ),
        conversation=types.SimpleNamespace(conversation_id="person_websocket_local_session"),
    )
    assert runner._get_external_conversation_id(ctx) == "550e8400-e29b-41d4-a716-446655440000"

    ctx.state.conversation = {}
    assert runner._get_external_conversation_id(ctx) == ""

    ctx.state.conversation = {"external.conversation_id": "person_websocket_local_session"}
    assert runner._get_external_conversation_id(ctx) == ""


def test_acp_resource_summary_includes_run_scoped_bridge_tools() -> None:
    from langbot_plugin.api.entities.builtin.agent_runner import (
        AgentEventContext,
        AgentInput,
        AgentResources,
        AgentRunContext,
        AgentRuntimeContext,
        AgentTrigger,
        ContextAccess,
        ContextAPICapabilities,
        DeliveryContext,
    )

    module = _load_runner_module("acp-agent-runner")
    ctx = AgentRunContext(
        run_id="run_1",
        trigger=AgentTrigger(type="message.received"),
        event=AgentEventContext(
            event_id="event_1",
            event_type="message.received",
            source="host_adapter",
        ),
        input=AgentInput(text="hello"),
        delivery=DeliveryContext(surface="webui"),
        resources=AgentResources.model_validate(
            {
                "knowledge_bases": [{"kb_id": "kb_1", "kb_name": "Docs"}],
                "tools": [{"tool_name": "weather", "description": "lookup weather"}],
            }
        ),
        context=ContextAccess(available_apis=ContextAPICapabilities(history_page=True)),
        runtime=AgentRuntimeContext(),
    )

    assert module._resource_summary(ctx)["mcp_bridge_tools"] == [
        {"tool_name": "langbot_get_current_event"},
        {"tool_name": "langbot_list_assets"},
        {"tool_name": "langbot_history_page"},
        {"tool_name": "langbot_retrieve_knowledge"},
        {"tool_name": "langbot_get_tool_detail"},
        {"tool_name": "langbot_call_tool"},
    ]


def test_external_service_runners_declare_minimal_plugin_storage_permission() -> None:
    for plugin_dir in PLUGIN_DIRS - {"acp-agent-runner", "claude-code-agent", "codex-agent", "dify-agent"}:
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


def test_external_runner_usage_normalizers_preserve_provider_usage() -> None:
    coze = _load_runner_module("coze-agent")
    assert coze._usage_from_payload({"usage": {"input_count": 11, "output_count": 7, "token_count": 18}}) == {
        "input_count": 11,
        "output_count": 7,
        "token_count": 18,
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }

    dify = _load_runner_module("dify-agent")
    assert dify._usage_from_payload(
        {
            "metadata": {
                "usage": {
                    "prompt_tokens": 13,
                    "completion_tokens": 5,
                    "total_tokens": 18,
                    "total_price": "0.0001",
                }
            }
        }
    ) == {
        "prompt_tokens": 13,
        "completion_tokens": 5,
        "total_tokens": 18,
        "total_price": "0.0001",
    }

    remove_dashscope_stub = "dashscope" not in sys.modules
    if remove_dashscope_stub:
        sys.modules["dashscope"] = types.SimpleNamespace(Application=object())
    try:
        dashscope = _load_runner_module("dashscope-agent")
    finally:
        if remove_dashscope_stub:
            sys.modules.pop("dashscope", None)
    assert dashscope._usage_from_payload({"usage": {"input_tokens": 3, "output_tokens": 4}}) == {
        "input_tokens": 3,
        "output_tokens": 4,
        "prompt_tokens": 3,
        "completion_tokens": 4,
        "total_tokens": 7,
    }

    tbox = _load_runner_module("tbox-agent")
    assert tbox._usage_from_payload({"data": {}}, {"usage": {"prompt_tokens": "2", "completion_tokens": "8"}}) == {
        "prompt_tokens": 2,
        "completion_tokens": 8,
        "total_tokens": 10,
    }


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
        "deerflow-agent": "_get_user_tag",
        "dify-agent": "_get_user_tag",
        "langflow-agent": "_get_user_tag",
        "n8n-agent": "_get_user_tag",
        "tbox-agent": "_get_user_id",
        "weknora-agent": "_get_user_tag",
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
