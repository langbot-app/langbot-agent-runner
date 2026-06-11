from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIRS = {
    "coze-agent",
    "dashscope-agent",
    "dify-agent",
    "langflow-agent",
    "litellm-agent-platform-agent",
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


def test_litellm_runner_declares_bridge_related_capabilities() -> None:
    litellm_runner = _load_yaml(ROOT / "litellm-agent-platform-agent" / "components" / "agent_runner" / "default.yaml")
    assert litellm_runner["spec"]["permissions"] == {
        "tools": ["detail", "call"],
        "knowledge_bases": ["retrieve"],
        "history": ["page"],
        "storage": ["plugin"],
    }
    assert litellm_runner["spec"]["capabilities"]["tool_calling"] is True
    assert litellm_runner["spec"]["capabilities"]["knowledge_retrieval"] is True


def test_external_service_runners_declare_minimal_plugin_storage_permission() -> None:
    for plugin_dir in PLUGIN_DIRS - {"litellm-agent-platform-agent"}:
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


def test_litellm_agent_platform_runner_creates_platform_session(monkeypatch) -> None:
    module = _load_runner_module("litellm-agent-platform-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    calls: list[tuple[str, tuple]] = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("init", (kwargs,)))

        async def create_platform_session(self, agent_id, *, title=""):
            calls.append(("create_platform_session", (agent_id, title)))
            return {"id": "lap_sess_1"}

        async def wait_platform_session_ready(self, session_id, *, timeout, poll_interval):
            calls.append(("wait_platform_session_ready", (session_id, timeout, poll_interval)))
            return {"id": session_id, "status": "ready"}

        async def send_platform_message_and_wait(self, session_id, text, *, model, timeout, poll_interval):
            calls.append(("send_platform_message_and_wait", (session_id, text, model, timeout, poll_interval)))
            return "platform ok", [{"parts": [{"type": "text", "text": "platform ok"}]}]

    monkeypatch.setattr(module, "AsyncLiteLLMAgentPlatformClient", FakeClient)
    ctx = _agent_run_context(
        text="run through platform",
        config={
            "api-mode": "agent-platform",
            "base-url": "http://platform.test",
            "api-key": "key",
            "agent-id": "agent_1",
            "session-ready-timeout": 5,
            "poll-interval": 1,
        },
    )
    ctx.state.conversation.clear()

    results = asyncio.run(_collect_results(runner, ctx))

    assert calls[:3] == [
        ("init", ({"base_url": "http://platform.test", "api_key": "key", "timeout": 300.0},)),
        ("create_platform_session", ("agent_1", "run through platform")),
        ("wait_platform_session_ready", ("lap_sess_1", 5.0, 1.0)),
    ]
    assert calls[3][0] == "send_platform_message_and_wait"
    assert calls[3][1][0] == "lap_sess_1"
    assert "Current LangBot run_id: run_1" in calls[3][1][1]
    assert "pass this exact run_id" in calls[3][1][1]
    assert "run through platform" in calls[3][1][1]
    assert calls[3][1][2:] == ("", 300.0, 1.0)
    assert [result.type.value for result in results] == [
        "message.completed",
        "state.updated",
        "run.completed",
    ]
    assert results[0].data["message"]["content"] == "platform ok"
    assert results[1].data == {
        "scope": "conversation",
        "key": "external.session_id",
        "value": "lap_sess_1",
    }
    assert results[2].data == {"finish_reason": "stop"}


def test_litellm_agent_platform_runner_supports_managed_agents_v0(monkeypatch) -> None:
    module = _load_runner_module("litellm-agent-platform-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    calls: list[tuple[str, tuple]] = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("init", (kwargs,)))

        async def create_managed_session(self, harness, model=""):
            calls.append(("create_managed_session", (harness, model)))
            return {"id": "mh_sess_1"}

        async def send_managed_message_and_wait(self, session_id, text, *, timeout, poll_interval):
            calls.append(("send_managed_message_and_wait", (session_id, text, timeout, poll_interval)))
            return "managed ok", [{"type": "agent.message", "content": [{"type": "text", "text": "managed ok"}]}]

    monkeypatch.setattr(module, "AsyncLiteLLMAgentPlatformClient", FakeClient)
    ctx = _agent_run_context(
        text="run through managed v0",
        config={
            "api-mode": "managed-agents-v0",
            "base-url": "http://harness.test",
            "harness": "codex",
            "model": "gpt-test",
            "timeout": 8,
            "poll-interval": 1,
        },
    )
    ctx.state.conversation.clear()

    results = asyncio.run(_collect_results(runner, ctx))

    assert calls[:2] == [
        ("init", ({"base_url": "http://harness.test", "api_key": "", "timeout": 8.0},)),
        ("create_managed_session", ("codex", "gpt-test")),
    ]
    assert calls[2][0] == "send_managed_message_and_wait"
    assert calls[2][1][0] == "mh_sess_1"
    assert "Current LangBot run_id: run_1" in calls[2][1][1]
    assert "pass this exact run_id" in calls[2][1][1]
    assert "run through managed v0" in calls[2][1][1]
    assert calls[2][1][2:] == (8.0, 1.0)
    assert [result.type.value for result in results] == [
        "message.completed",
        "state.updated",
        "state.updated",
        "run.completed",
    ]
    assert results[0].data["message"]["content"] == "managed ok"
    assert results[1].data == {
        "scope": "conversation",
        "key": "external.managed_session_id",
        "value": "mh_sess_1",
    }
    assert results[2].data == {
        "scope": "conversation",
        "key": "external.session_id",
        "value": "mh_sess_1",
    }
    assert results[3].data == {"finish_reason": "stop"}


def test_litellm_agent_platform_runner_managed_v0_real_http_smoke(monkeypatch) -> None:
    from http.server import BaseHTTPRequestHandler
    from socketserver import TCPServer

    for key in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "http_proxy", "https_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    module = _load_runner_module("litellm-agent-platform-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    events: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/v1/sessions":
                assert body["agent"] == "codex"
                self._json(201, {"id": "http_sess_1", "object": "session"})
                return
            if self.path == "/v1/sessions/http_sess_1/events":
                event_text = body["events"][0]["content"][0]["text"]
                assert "Current LangBot run_id: run_1" in event_text
                assert "real http smoke" in event_text
                events.extend(
                    [
                        {"type": "user.message", "content": body["events"][0]["content"]},
                        {"type": "agent.message", "content": [{"type": "text", "text": "real http ok"}]},
                        {"type": "session.status_idle"},
                    ]
                )
                self._json(200, {"ok": True})
                return
            self._json(404, {"error": {"message": "not found"}})

        def do_GET(self) -> None:
            if self.path == "/v1/sessions/http_sess_1":
                self._json(200, {"id": "http_sess_1", "object": "session"})
                return
            if self.path == "/v1/sessions/http_sess_1/events":
                self._json(200, {"object": "list", "data": events})
                return
            self._json(404, {"error": {"message": "not found"}})

        def log_message(self, *_args) -> None:
            return

    server = TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        ctx = _agent_run_context(
            text="real http smoke",
            config={
                "api-mode": "managed-agents-v0",
                "base-url": base_url,
                "harness": "codex",
                "timeout": 5,
                "poll-interval": 0.1,
            },
        )
        ctx.state.conversation.clear()

        results = asyncio.run(_collect_results(runner, ctx))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert [result.type.value for result in results] == [
        "message.completed",
        "state.updated",
        "state.updated",
        "run.completed",
    ]
    assert results[0].data["message"]["content"] == "real http ok"
    assert results[1].data["value"] == "http_sess_1"
    assert results[3].data == {"finish_reason": "stop"}


def test_litellm_agent_platform_stable_mcp_gateway_uses_run_scoped_host_actions() -> None:
    for module_name in list(sys.modules):
        if module_name == "pkg" or module_name.startswith("pkg."):
            del sys.modules[module_name]

    plugin_root = ROOT / "litellm-agent-platform-agent"
    sys.path.insert(0, str(plugin_root))
    try:
        from pkg.langbot_mcp_gateway import LangBotMCPGateway
    finally:
        sys.path.remove(str(plugin_root))

    class FakeRuntimeHandler:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, float | None]] = []

        async def call_action(self, action, data, timeout=None):
            action_name = getattr(action, "value", str(action))
            self.calls.append((action_name, data, timeout))
            if data.get("run_id") != "run_1":
                raise RuntimeError(f"Run session {data.get('run_id')} not found or expired")
            if action_name == "history_page":
                return {"items": [{"text": "history-from-host"}], "has_more": False}
            if action_name == "retrieve_knowledge_base":
                return {
                    "results": [
                        {
                            "content": f"{data['kb_id']}:{data['query_text']}:{data['top_k']}",
                            "filters": data["filters"],
                        }
                    ]
                }
            if action_name == "call_tool":
                return {
                    "result": {
                        "tool_name": data["tool_name"],
                        "parameters": data["parameters"],
                    }
                }
            raise AssertionError(f"unexpected action: {action_name}")

    class FakePlugin:
        def __init__(self) -> None:
            self.plugin_runtime_handler = FakeRuntimeHandler()

    def post(url: str, token: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    async def run_probe() -> tuple[dict, dict, dict, dict, dict, int, dict, list[tuple[str, dict, float | None]]]:
        plugin = FakePlugin()
        gateway = LangBotMCPGateway(
            plugin,
            host="127.0.0.1",
            port=0,
            token="secret",
        )
        gateway.start()
        try:
            initialized = await asyncio.to_thread(
                post,
                gateway.endpoint,
                "secret",
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            tools = await asyncio.to_thread(
                post,
                gateway.endpoint,
                "secret",
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            history = await asyncio.to_thread(
                post,
                gateway.endpoint,
                "secret",
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "langbot_history_page",
                        "arguments": {"run_id": "run_1", "limit": 2},
                    },
                },
            )
            retrieved = await asyncio.to_thread(
                post,
                gateway.endpoint,
                "secret",
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "langbot_retrieve_knowledge",
                        "arguments": {
                            "run_id": "run_1",
                            "kb_id": "kb_allowed",
                            "query_text": "hello",
                            "top_k": 2,
                        },
                    },
                },
            )
            called = await asyncio.to_thread(
                post,
                gateway.endpoint,
                "secret",
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "langbot_call_tool",
                        "arguments": {
                            "run_id": "run_1",
                            "tool_name": "weather",
                            "parameters": {"city": "Shanghai"},
                        },
                    },
                },
            )
            wrong_run = await asyncio.to_thread(
                post,
                gateway.endpoint,
                "secret",
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {
                        "name": "langbot_retrieve_knowledge",
                        "arguments": {"run_id": "bad_run", "kb_id": "kb_allowed", "query_text": "nope"},
                    },
                },
            )

            def unauthorized() -> int:
                try:
                    post(gateway.endpoint, "bad-token", {"jsonrpc": "2.0", "id": 7, "method": "ping"})
                except urllib.error.HTTPError as exc:
                    return exc.code
                return 200

            status = await asyncio.to_thread(unauthorized)
            return initialized, tools, history, retrieved, called, status, wrong_run, plugin.plugin_runtime_handler.calls
        finally:
            gateway.stop()

    initialized, tools, history, retrieved, called, status, wrong_run, calls = asyncio.run(run_probe())

    assert initialized["result"]["serverInfo"]["name"] == "langbot-agent-platform-gateway"
    assert {tool["name"] for tool in tools["result"]["tools"]} == {
        "langbot_history_page",
        "langbot_retrieve_knowledge",
        "langbot_call_tool",
    }
    assert history["result"]["structuredContent"]["items"][0]["text"] == "history-from-host"
    assert retrieved["result"]["structuredContent"]["result"][0]["content"] == "kb_allowed:hello:2"
    assert called["result"]["structuredContent"]["tool_name"] == "weather"
    assert calls[0] == (
        "history_page",
        {
            "run_id": "run_1",
            "conversation_id": None,
            "before_cursor": None,
            "after_cursor": None,
            "limit": 2,
            "direction": "backward",
            "include_artifacts": False,
        },
        30,
    )
    assert calls[1][0] == "retrieve_knowledge_base"
    assert calls[1][1]["run_id"] == "run_1"
    assert calls[2][0] == "call_tool"
    assert calls[2][1]["run_id"] == "run_1"
    assert wrong_run["error"]["message"] == "Run session bad_run not found or expired"
    assert status == 401


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
