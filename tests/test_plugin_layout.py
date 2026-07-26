from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
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
    AgentRunState,
    AgentRuntimeContext,
    AgentTrigger,
    DeliveryContext,
    InteractionSubmission,
)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAMES = {
    "acp-agent-runner": "ACPAgentRunner",
    "claude-code-agent": "ClaudeCodeAgent",
    "codex-agent": "CodexAgent",
    "coze-agent": "CozeAgent",
    "dashscope-agent": "DashScopeAgent",
    "deerflow-agent": "DeerFlowAgent",
    "dify-agent": "DifyAgent",
    "langflow-agent": "LangflowAgent",
    "n8n-agent": "N8nAgent",
    "tbox-agent": "TboxAgent",
    "weknora-agent": "WeKnoraAgent",
}
PLUGIN_DIRS = set(PLUGIN_NAMES)
MARKETPLACE_LOCALES = {
    "en_US",
    "zh_Hans",
    "zh_Hant",
    "ja_JP",
    "th_TH",
    "vi_VN",
    "es_ES",
    "ru_RU",
}
SUPPORTED_FORM_TYPES = {
    "array[string]",
    "boolean",
    "integer",
    "json",
    "number",
    "secret",
    "select",
    "string",
    "text",
}
SENSITIVE_CONFIG_FIELDS = {
    "api-key",
    "auth-header",
    "basic-password",
    "daemon-token",
    "header-value",
    "jwt-secret",
}


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_runner_module(plugin_dir: str):
    return _load_plugin_module(plugin_dir, "components/agent_runner/default.py", "runner")


def _load_plugin_module(plugin_dir: str, relative_path: str, suffix: str):
    for module_name in list(sys.modules):
        if module_name == "pkg" or module_name.startswith("pkg."):
            del sys.modules[module_name]

    plugin_root = ROOT / plugin_dir
    sys.path.insert(0, str(plugin_root))
    try:
        module_path = plugin_root / relative_path
        spec = importlib.util.spec_from_file_location(f"test_{plugin_dir.replace('-', '_')}_{suffix}", module_path)
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

        assert manifest["metadata"]["author"] == "langbot-team"
        assert manifest["metadata"]["name"] == PLUGIN_NAMES[plugin_dir]
        assert re.fullmatch(r"[A-Z][A-Za-z0-9]*", manifest["metadata"]["name"])
        assert runner["apiVersion"] == "langbot/v1"
        assert runner["kind"] == "AgentRunner"
        assert runner["metadata"]["name"] == "default"
        assert runner["metadata"]["label"]["en_US"] != "Default"
        assert runner["metadata"]["label"]["zh_Hans"] != "默认"
        assert "protocol_version" not in runner["spec"]
        assert runner["execution"]["python"]["path"] == "default.py"
        assert runner["execution"]["python"]["attr"] == "DefaultAgentRunner"


def test_plugins_have_publishable_marketplace_metadata() -> None:
    expected_readmes = {f"README_{locale}.md" for locale in MARKETPLACE_LOCALES}

    for plugin_dir in PLUGIN_DIRS:
        plugin_root = ROOT / plugin_dir
        manifest = _load_yaml(plugin_root / "manifest.yaml")
        runner = _load_yaml(plugin_root / "components" / "agent_runner" / "default.yaml")
        metadata = manifest["metadata"]
        runner_id = f"plugin:{metadata['author']}/{metadata['name']}/{runner['metadata']['name']}"
        config_fields = [
            item["name"]
            for item in manifest["spec"].get("config", []) + runner["spec"].get("config", [])
        ]

        assert manifest["apiVersion"] == "v1"
        assert "version" not in manifest["spec"]
        assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", metadata["version"])
        assert metadata["repository"] == "https://github.com/langbot-app/langbot-agent-runner"
        assert set(metadata["label"]) == MARKETPLACE_LOCALES
        assert set(metadata["description"]) == MARKETPLACE_LOCALES
        root_readme = (plugin_root / "README.md").read_text(encoding="utf-8")
        english_readme = (plugin_root / "readme" / "README_en_US.md").read_text(encoding="utf-8")
        zh_hans_readme = (plugin_root / "readme" / "README_zh_Hans.md").read_text(encoding="utf-8")
        assert root_readme == english_readme
        assert len(root_readme.encode("utf-8")) >= 1_000
        assert any("\u4e00" <= char <= "\u9fff" for char in zh_hans_readme)
        assert all(field in root_readme for field in config_fields)
        assert runner_id in root_readme
        assert {path.name for path in (plugin_root / "readme").glob("README_*.md")} == expected_readmes
        for readme_name in expected_readmes:
            localized_readme = (plugin_root / "readme" / readme_name).read_text(encoding="utf-8")
            assert len(localized_readme.encode("utf-8")) >= 1_000
            assert all(field in localized_readme for field in config_fields)
            assert runner_id in localized_readme


def test_plugin_forms_use_supported_types_and_mask_sensitive_values() -> None:
    for plugin_dir in PLUGIN_DIRS:
        plugin_root = ROOT / plugin_dir
        plugin_manifest = _load_yaml(plugin_root / "manifest.yaml")
        runner_manifest = _load_yaml(plugin_root / "components" / "agent_runner" / "default.yaml")
        fields = plugin_manifest["spec"].get("config", []) + runner_manifest["spec"].get("config", [])

        for field in fields:
            assert field["type"] in SUPPORTED_FORM_TYPES, (plugin_dir, field["name"], field["type"])
            if field["name"] in SENSITIVE_CONFIG_FIELDS:
                assert field["type"] == "secret", (plugin_dir, field["name"])


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
    assert acp_runner["spec"]["capabilities"]["multimodal_input"] is True

    dify_runner = _load_yaml(ROOT / "dify-agent" / "components" / "agent_runner" / "default.yaml")
    assert dify_runner["spec"]["permissions"] == {
        "tools": ["detail", "call"],
        "knowledge_bases": ["retrieve"],
        "history": ["page"],
        "storage": ["plugin"],
        "interactions": ["request"],
    }
    assert dify_runner["spec"]["capabilities"]["tool_calling"] is True
    assert dify_runner["spec"]["capabilities"]["knowledge_retrieval"] is True
    assert dify_runner["spec"]["capabilities"]["interactions"] is True


def test_dify_runner_exposes_guided_and_masked_secret_config() -> None:
    runner = _load_yaml(ROOT / "dify-agent" / "components" / "agent_runner" / "default.yaml")
    config = {item["name"]: item for item in runner["spec"]["config"]}

    assert config["api-key"]["type"] == "secret"
    assert config["base-prompt"]["type"] == "text"
    assert config["base-url"]["description"]["en_US"]
    assert config["app-type"]["description"]["zh_Hans"]

    advanced_fields = {
        "langbot-assets-gateway-host",
        "langbot-assets-gateway-port",
        "langbot-assets-gateway-request-timeout",
        "langbot-assets-token-ttl",
        "langbot-assets-input-name",
    }
    for field_name in advanced_fields:
        assert config[field_name]["show_if"] == {
            "field": "langbot-assets-enabled",
            "operator": "eq",
            "value": True,
        }


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


def test_acp_prompt_blocks_include_runtime_supported_image_data() -> None:
    module = _load_plugin_module("acp-agent-runner", "pkg/prompt.py", "prompt")

    blocks = module.acp_prompt_blocks(
        "Describe the image.",
        {
            "attachments": [
                {
                    "type": "image",
                    "content": "data:image/png;base64,aGVsbG8=",
                }
            ]
        },
        {"image": True, "audio": False, "embedded_context": False},
    )

    assert blocks == [
        {"type": "text", "text": "Describe the image."},
        {"type": "image", "mimeType": "image/png", "data": "aGVsbG8="},
    ]


def test_acp_prompt_blocks_use_resource_link_for_url_images_without_image_capability() -> None:
    module = _load_plugin_module("acp-agent-runner", "pkg/prompt.py", "prompt")

    blocks = module.acp_prompt_blocks(
        "Check the attachment.",
        {
            "contents": [
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.com/a.png"},
                }
            ]
        },
        {"image": False, "audio": False, "embedded_context": False},
    )

    assert blocks == [
        {"type": "text", "text": "Check the attachment."},
        {
            "type": "resource_link",
            "uri": "https://example.com/a.png",
            "name": "image",
            "mimeType": "image/png",
        },
    ]


def test_acp_prompt_blocks_embed_text_file_when_runtime_supports_embedded_context() -> None:
    module = _load_plugin_module("acp-agent-runner", "pkg/prompt.py", "prompt")

    blocks = module.acp_prompt_blocks(
        "Read the file.",
        {
            "contents": [
                {
                    "type": "file_base64",
                    "file_base64": "data:text/plain;base64,aGk=",
                    "file_name": "hello.txt",
                }
            ]
        },
        {"image": False, "audio": False, "embedded_context": True},
    )

    assert blocks == [
        {"type": "text", "text": "Read the file."},
        {
            "type": "resource",
            "resource": {
                "uri": "langbot-input://hello.txt",
                "mimeType": "text/plain",
                "text": "hi",
            },
        },
    ]


def test_acp_prompt_blocks_note_unsupported_inline_image() -> None:
    module = _load_plugin_module("acp-agent-runner", "pkg/prompt.py", "prompt")

    blocks = module.acp_prompt_blocks(
        "",
        {"attachments": [{"type": "image", "content": "data:image/png;base64,aGVsbG8="}]},
        {"image": False, "audio": False, "embedded_context": False},
    )

    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    assert "image attachment(s) were not sent" in blocks[0]["text"]


def test_acp_runner_declares_daemon_location() -> None:
    acp_manifest = _load_yaml(ROOT / "acp-agent-runner" / "manifest.yaml")
    plugin_config_names = {item["name"] for item in acp_manifest["spec"]["config"]}
    assert {"daemon-enabled", "daemon-host", "daemon-port", "daemon-token"} <= plugin_config_names

    acp_runner = _load_yaml(ROOT / "acp-agent-runner" / "components" / "agent_runner" / "default.yaml")
    location_config = next(item for item in acp_runner["spec"]["config"] if item["name"] == "location")
    assert {option["name"] for option in location_config["options"]} == {"local", "remote-ssh", "daemon"}
    assert any(item["name"] == "daemon-id" for item in acp_runner["spec"]["config"])


def test_acp_runner_validates_daemon_location_config() -> None:
    module = _load_runner_module("acp-agent-runner")
    runner = object.__new__(module.DefaultAgentRunner)
    runner.get_plugin_config = lambda: {
        "daemon-host": "0.0.0.0",
        "daemon-port": 18766,
        "daemon-token": "secret",
    }

    ctx = types.SimpleNamespace(
        config={
            "provider": "codex",
            "location": "daemon",
            "daemon-id": "alice-laptop",
            "workspace": "/tmp/project",
        }
    )
    config = runner._validate_config(ctx)

    assert config["location"] == "daemon"
    assert config["daemon_id"] == "alice-laptop"
    assert config["daemon_hub"] == {
        "enabled": False,
        "host": "0.0.0.0",
        "port": 18766,
        "token": "secret",
    }

    ctx.config["daemon-id"] = ""
    try:
        runner._validate_config(ctx)
    except module.AcpError as exc:
        assert exc.code == "acp.config_invalid"
        assert "daemon-id is required" in exc.message
    else:
        raise AssertionError("daemon-id should be required when location=daemon")


def test_acp_daemon_tool_events_match_agent_run_result_schema() -> None:
    import asyncio

    from langbot_plugin.api.entities.builtin.agent_runner import AgentRunResult

    module = _load_plugin_module("acp-agent-runner", "daemon.py", "daemon")
    daemon = object.__new__(module.RunnerDaemon)
    events = []

    async def fake_emit(job_id, event):
        events.append((job_id, event))

    daemon.emit_event = fake_emit

    asyncio.run(
        daemon._emit_tool_update(
            "job-1",
            {"toolCallId": "tool-1", "name": "read_file", "status": "pending"},
            set(),
        )
    )

    assert events == [
        (
            "job-1",
            {
                "type": "tool.call.started",
                "data": {
                    "tool_call_id": "tool-1",
                    "tool_name": "read_file",
                    "parameters": {},
                },
            },
        )
    ]

    event = dict(events[0][1])
    event["run_id"] = "run-1"
    AgentRunResult.model_validate(event)


def test_acp_runner_uses_sdk_mcp_bridge_helper(monkeypatch) -> None:
    module = _load_runner_module("acp-agent-runner")
    calls = {}

    class FakeAccess:
        def __init__(
            self,
            api,
            ctx,
            *,
            enabled,
            location,
            mode,
            transport,
            bridge_host,
            bridge_port,
            bridge_public_url,
            bridge_request_timeout,
            gateway_host,
            gateway_port,
            gateway_public_url,
            gateway_request_timeout,
            gateway_token_ttl,
        ):
            calls["api"] = api
            calls["ctx"] = ctx
            calls["enabled"] = enabled
            calls["location"] = location
            calls["mode"] = mode
            calls["transport"] = transport
            calls["bridge_host"] = bridge_host
            calls["bridge_port"] = bridge_port
            calls["bridge_public_url"] = bridge_public_url
            calls["bridge_request_timeout"] = bridge_request_timeout
            calls["gateway_host"] = gateway_host
            calls["gateway_port"] = gateway_port
            calls["gateway_public_url"] = gateway_public_url
            calls["gateway_request_timeout"] = gateway_request_timeout
            calls["gateway_token_ttl"] = gateway_token_ttl
            self.server_config = module.AgentMCPServerConfig.stdio(
                name="langbot_agent",
                command="python",
                args=["-m", "langbot_plugin.api.agent_tools.mcp_stdio"],
                env={"LANGBOT_AGENT_MCP_ENDPOINT": "http://127.0.0.1:12345"},
            )
            self.reverse_tunnel = None

        def start(self):
            calls["started"] = True

    runner = object.__new__(module.DefaultAgentRunner)
    runner.get_run_api = lambda ctx: "run-api"
    ctx = object()

    monkeypatch.setattr(module, "AgentRunMCPAccess", FakeAccess)
    access, servers = runner._mcp_servers(
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
            "asset_gateway_host": "127.0.0.1",
            "asset_gateway_port": 0,
            "asset_gateway_request_timeout": 60.0,
            "asset_gateway_token_ttl": 3600.0,
            "asset_gateway_public_url": "",
        },
    )

    assert isinstance(access, FakeAccess)
    assert calls == {
        "api": "run-api",
        "ctx": ctx,
        "enabled": True,
        "location": "local",
        "mode": "ephemeral",
        "transport": "stdio",
        "bridge_host": "127.0.0.1",
        "bridge_port": 0,
        "bridge_public_url": "",
        "bridge_request_timeout": 15.0,
        "gateway_host": "127.0.0.1",
        "gateway_port": 0,
        "gateway_public_url": "",
        "gateway_request_timeout": 60.0,
        "gateway_token_ttl": 3600.0,
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

    class FakeAccess:
        def __init__(
            self,
            api,
            ctx,
            *,
            enabled,
            location,
            mode,
            transport,
            bridge_host,
            bridge_port,
            bridge_public_url,
            bridge_request_timeout,
            gateway_host,
            gateway_port,
            gateway_public_url,
            gateway_request_timeout,
            gateway_token_ttl,
        ):
            calls["api"] = api
            calls["ctx"] = ctx
            calls["enabled"] = enabled
            calls["location"] = location
            calls["mode"] = mode
            calls["transport"] = transport
            calls["bridge_host"] = bridge_host
            calls["bridge_port"] = bridge_port
            calls["bridge_public_url"] = bridge_public_url
            calls["bridge_request_timeout"] = bridge_request_timeout
            calls["gateway_host"] = gateway_host
            calls["gateway_port"] = gateway_port
            calls["gateway_public_url"] = gateway_public_url
            calls["gateway_request_timeout"] = gateway_request_timeout
            calls["gateway_token_ttl"] = gateway_token_ttl
            self.server_config = module.AgentMCPServerConfig.http(
                name="langbot_agent",
                url=gateway_public_url,
                headers={"Authorization": "Bearer token_1"},
            )
            self.reverse_tunnel = None

        def start(self):
            calls["started"] = True

    runner = object.__new__(module.DefaultAgentRunner)
    runner.get_run_api = lambda ctx: "run-api"
    ctx = object()

    monkeypatch.setattr(module, "AgentRunMCPAccess", FakeAccess)
    access, servers = runner._mcp_servers(
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

    assert isinstance(access, FakeAccess)
    assert calls == {
        "api": "run-api",
        "ctx": ctx,
        "enabled": True,
        "location": "local",
        "mode": "gateway",
        "transport": "auto",
        "bridge_host": "127.0.0.1",
        "bridge_port": 0,
        "bridge_public_url": "",
        "bridge_request_timeout": 15.0,
        "gateway_host": "127.0.0.1",
        "gateway_port": 8765,
        "gateway_public_url": "http://gateway.example/mcp",
        "gateway_request_timeout": 12.0,
        "gateway_token_ttl": 120.0,
        "started": True,
    }
    assert servers == [
        {
            "name": "langbot_agent",
            "type": "http",
            "url": "http://gateway.example/mcp",
            "headers": [{"name": "Authorization", "value": "Bearer token_1"}],
        }
    ]


def _native_ctx(
    config: dict,
    *,
    text: str = "hello native",
    conversation_state: dict | None = None,
    interaction: InteractionSubmission | None = None,
) -> AgentRunContext:
    return AgentRunContext(
        run_id="run_native",
        trigger=AgentTrigger(type="message.received"),
        event=AgentEventContext(event_id="evt_1", event_type="message.received", source="test"),
        input=AgentInput(text=text, interaction=interaction),
        delivery=DeliveryContext(surface="test"),
        resources=AgentResources(),
        state=AgentRunState(conversation=conversation_state or {}),
        runtime=AgentRuntimeContext(),
        config=config,
    )


def _write_fake_native_cli(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import json",
                "import os",
                "import sys",
                "session_id = 'missing-session'",
                "if '--session-id' in sys.argv:",
                "    session_id = sys.argv[sys.argv.index('--session-id') + 1]",
                "prompt = sys.stdin.read()",
                "if 'hello native' in sys.argv:",
                "    print('prompt leaked into argv', file=sys.stderr)",
                "    raise SystemExit(2)",
                "if '--mcp-config' in sys.argv:",
                "    config_path = sys.argv[sys.argv.index('--mcp-config') + 1]",
                "    if config_path.startswith('{'):",
                "        print('mcp config passed as raw json', file=sys.stderr)",
                "        raise SystemExit(3)",
                "    if os.name != 'nt' and os.stat(config_path).st_mode & 0o777 != 0o600:",
                "        print('mcp config mode is not 0600', file=sys.stderr)",
                "        raise SystemExit(4)",
                "    config = open(config_path, encoding='utf-8').read()",
                "    servers = json.loads(config).get('mcpServers', {})",
                "    if len(servers) > 1 and 'Bearer test-secret-token' not in config:",
                "        print('mcp config file missing expected token', file=sys.stderr)",
                "        raise SystemExit(5)",
                "    if 'Bearer test-secret-token' in ' '.join(sys.argv):",
                "        print('mcp token leaked into argv', file=sys.stderr)",
                "        raise SystemExit(6)",
                "print(json.dumps({'type': 'session.started', 'session_id': session_id}), flush=True)",
                "if 'ASK_INTERACTION' in prompt:",
                "    print(json.dumps({'type': 'assistant', 'session_id': session_id, 'message': {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'toolu-question-1', 'name': 'mcp__langbot_agent__ask_user_question', 'input': {'title': 'Need details', 'questions': [{'id': 'environment', 'question': 'Choose an environment', 'options': [{'label': 'staging', 'description': 'Use staging'}, {'label': 'production', 'description': 'Use production'}], 'multiSelect': False}]}}]}}), flush=True)",
                "    print(json.dumps({'type': 'message.completed', 'text': 'WAITING_FOR_INTERACTION'}), flush=True)",
                "else:",
                "    print(json.dumps({'type': 'message.completed', 'text': 'FAKE_NATIVE_OK:' + prompt}), flush=True)",
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
                "        if 'ASK_INTERACTION' in prompt:",
                "            send({'jsonrpc': '2.0', 'id': 700, 'method': 'item/tool/call', 'params': {'threadId': thread_id, 'turnId': 'turn-1', 'callId': 'call-question-1', 'tool': 'ask_user_question', 'arguments': {'title': 'Need details', 'questions': [{'id': 'environment', 'question': 'Choose an environment', 'options': [{'label': 'staging', 'value': 'staging'}, {'label': 'production', 'value': 'production'}]}]}}})",
                "            continue",
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
        "run.completed",
    ]
    assert results[1].data["chunk"]["content"] == "FAKE_NATIVE_OK:hello native"
    assert "message" not in results[2].data
    assert results[0].data["key"] == "external.claude_code_session_id"


def test_claude_code_runner_passes_mcp_config_by_temp_file(tmp_path: Path) -> None:
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
            "mcp-servers-json": [
                {
                    "name": "langbot",
                    "type": "http",
                    "url": "http://127.0.0.1:1234/mcp",
                    "headers": {"Authorization": "Bearer test-secret-token"},
                }
            ],
        }
    )
    results = asyncio.run(_collect_async(runner.run(ctx)))

    assert [item.type for item in results] == [
        "state.updated",
        "message.delta",
        "run.completed",
    ]
    assert results[1].data["chunk"]["content"] == "FAKE_NATIVE_OK:hello native"
    assert "message" not in results[2].data


def test_claude_code_runner_non_streaming_emits_one_message(tmp_path: Path) -> None:
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
            "streaming": False,
        }
    )
    results = asyncio.run(_collect_async(runner.run(ctx)))

    assert [item.type for item in results] == [
        "state.updated",
        "message.completed",
        "run.completed",
    ]
    assert results[1].data["message"]["content"] == "FAKE_NATIVE_OK:hello native"
    assert "message" not in results[2].data


def test_claude_code_runner_pauses_for_question_and_resumes_with_tool_result(tmp_path: Path) -> None:
    fake_cli = tmp_path / "fake_native_cli.py"
    _write_fake_native_cli(fake_cli)
    module = _load_runner_module("claude-code-agent")
    native = sys.modules["pkg.native_cli"]
    runner = object.__new__(module.DefaultAgentRunner)
    runner.get_plugin_config = lambda: {}
    runner.get_run_api = lambda ctx: None
    config = {
        "location": "local",
        "command": sys.executable,
        "args-json": [str(fake_cli)],
        "workspace": str(tmp_path),
        "langbot-assets-enabled": False,
    }

    paused = asyncio.run(_collect_async(runner.run(_native_ctx(config, text="ASK_INTERACTION"))))

    assert [item.type for item in paused] == ["state.updated", "state.updated", "action.requested"]
    pending = paused[1].data["value"]
    request = paused[2].data["payload"]
    assert paused[2].data["action"] == "interaction.requested"
    assert request["fields"][0]["type"] == "select"
    assert request["fields"][0]["label"] == "Choose an environment"

    resumed = asyncio.run(
        _collect_async(
            runner.run(
                _native_ctx(
                    config,
                    conversation_state={
                        native.SESSION_STATE_KEY: "fake-session",
                        native.PENDING_INTERACTION_STATE_KEY: pending,
                    },
                    interaction=InteractionSubmission(
                        interaction_id=request["interaction_id"],
                        action_id="submit",
                        values={"environment": "staging"},
                    ),
                )
            )
        )
    )

    assert resumed[0].data == {
        "key": native.PENDING_INTERACTION_STATE_KEY,
        "value": None,
        "scope": "conversation",
    }
    assert resumed[-1].type == "run.completed"
    message_text = "".join(
        str(item.data.get("chunk", {}).get("content") or "")
        for item in resumed
        if item.type == "message.delta"
    )
    assert "authoritative response" in message_text
    assert '"answer": "staging"' in message_text


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
    assert "message" not in results[2].data
    assert results[0].data["key"] == "external.codex_session_id"
    assert results[0].data["value"] == "thread-123"
    assert [item.sequence for item in results] == [1, 2, 3]


def test_codex_runner_pauses_for_dynamic_question_and_resumes_thread(tmp_path: Path) -> None:
    fake_cli = tmp_path / "fake_codex_app_server.py"
    _write_fake_codex_app_server(fake_cli)
    module = _load_runner_module("codex-agent")
    native = sys.modules["pkg.native_cli"]
    runner = object.__new__(module.DefaultAgentRunner)
    runner.get_plugin_config = lambda: {}
    runner.get_run_api = lambda ctx: None
    config = {
        "location": "local",
        "command": f"{sys.executable} {fake_cli}",
        "workspace": str(tmp_path),
        "langbot-assets-enabled": False,
    }

    paused = asyncio.run(_collect_async(runner.run(_native_ctx(config, text="ASK_INTERACTION"))))

    assert [item.type for item in paused] == ["state.updated", "state.updated", "action.requested"]
    pending = paused[1].data["value"]
    request = paused[2].data["payload"]
    assert paused[2].data["action"] == "interaction.requested"
    assert request["fields"][0]["type"] == "select"
    assert pending["provider_call_id"] == "call-question-1"

    resumed = asyncio.run(
        _collect_async(
            runner.run(
                _native_ctx(
                    config,
                    conversation_state={
                        native.SESSION_STATE_KEY: "thread-123",
                        native.PENDING_INTERACTION_STATE_KEY: pending,
                    },
                    interaction=InteractionSubmission(
                        interaction_id=request["interaction_id"],
                        action_id="submit",
                        values={"environment": "production"},
                    ),
                )
            )
        )
    )

    assert resumed[0].data["key"] == native.PENDING_INTERACTION_STATE_KEY
    assert resumed[0].data["value"] is None
    assert resumed[-1].type == "run.completed"
    assert any(
        "production" in str(item.data.get("chunk", {}).get("content") or "")
        for item in resumed
        if item.type == "message.delta"
    )


def test_codex_runner_non_streaming_emits_one_message(tmp_path: Path) -> None:
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
            "streaming": False,
        }
    )
    results = asyncio.run(_collect_async(runner.run(ctx)))

    assert [item.type for item in results] == [
        "state.updated",
        "message.completed",
        "run.completed",
    ]
    assert results[1].data["message"]["content"] == "FAKE_CODEX_APP_SERVER_OK:hello native"
    assert "message" not in results[2].data
    assert [item.sequence for item in results] == [1, 2, 3]


def test_codex_local_home_is_unique_per_resumed_run(tmp_path: Path) -> None:
    module = _load_plugin_module("codex-agent", "pkg/native_cli.py", "native_cli_home")
    shared_home = tmp_path / "shared-codex"
    shared_home.mkdir()
    (shared_home / "auth.json").write_text("{}", encoding="utf-8")
    (shared_home / "sessions").mkdir()
    (shared_home / "sessions" / "shared-session.jsonl").write_text("session", encoding="utf-8")
    workspace = tmp_path / "workspace"

    env_one, _ = module._prepare_local_codex_home(
        str(workspace),
        "thread-123",
        {"CODEX_HOME": str(shared_home)},
        "",
    )
    first_home = Path(env_one["CODEX_HOME"])
    marker = first_home / "marker.txt"
    marker.write_text("keep", encoding="utf-8")
    env_two, _ = module._prepare_local_codex_home(
        str(workspace),
        "thread-123",
        {"CODEX_HOME": str(shared_home)},
        "",
    )
    second_home = Path(env_two["CODEX_HOME"])

    assert first_home != second_home
    assert first_home.exists()
    assert second_home.exists()
    assert marker.read_text(encoding="utf-8") == "keep"
    assert (first_home / "sessions" / "shared-session.jsonl").exists()
    assert (second_home / "sessions" / "shared-session.jsonl").exists()
    assert (shared_home / "sessions" / "shared-session.jsonl").read_text(encoding="utf-8") == "session"


def test_codex_detects_persisted_tool_calls_without_outputs(tmp_path: Path) -> None:
    module = _load_plugin_module("codex-agent", "pkg/native_cli.py", "native_cli_session_validation")
    shared_home = tmp_path / "shared-codex"
    sessions = shared_home / "sessions" / "2026" / "07" / "21"
    sessions.mkdir(parents=True)
    session_id = "thread-123"
    rollout = sessions / f"rollout-2026-07-21T20-00-00-{session_id}.jsonl"
    records = [
        {"payload": {"type": "custom_tool_call", "call_id": "call-complete"}},
        {"payload": {"type": "custom_tool_call_output", "call_id": "call-complete"}},
        {"payload": {"type": "function_call", "call_id": "call-pending"}},
    ]
    rollout.write_text(
        "\n".join(json.dumps(record) for record in records),
        encoding="utf-8",
    )

    assert module._pending_session_tool_calls(shared_home, session_id) == {"call-pending"}


def test_codex_app_server_accepts_json_lines_larger_than_asyncio_default(tmp_path: Path) -> None:
    module = _load_plugin_module("codex-agent", "pkg/native_cli.py", "native_cli_large_line")
    fake_cli = tmp_path / "fake_codex_large_line.py"
    fake_cli.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "thread_id = 'thread-large-line'",
                "def send(payload):",
                "    print(json.dumps(payload), flush=True)",
                "for line in sys.stdin:",
                "    request = json.loads(line)",
                "    method = request.get('method')",
                "    request_id = request.get('id')",
                "    if request_id is not None and method == 'initialize':",
                "        send({'jsonrpc': '2.0', 'id': request_id, 'result': {}})",
                "    elif request_id is not None and method == 'thread/start':",
                "        send({'jsonrpc': '2.0', 'id': request_id, 'result': {'threadId': thread_id}})",
                "    elif request_id is not None and method == 'turn/start':",
                "        send({'jsonrpc': '2.0', 'id': request_id, 'result': {}})",
                "        send({'jsonrpc': '2.0', 'method': 'turn/started', 'params': {'threadId': thread_id}})",
                "        text = 'x' * 70000",
                "        send({'jsonrpc': '2.0', 'method': 'item/completed', 'params': {'threadId': thread_id, 'item': {'id': 'large', 'type': 'agentMessage', 'text': text, 'phase': 'final_answer'}}})",
                "        send({'jsonrpc': '2.0', 'method': 'turn/completed', 'params': {'threadId': thread_id, 'turn': {'status': 'completed'}}})",
            ]
        ),
        encoding="utf-8",
    )

    async def collect():
        return [
            event
            async for event in module._run_cli_process_events(
                sys.executable,
                [str(fake_cli), "app-server", "--listen", "stdio://"],
                cwd=str(tmp_path),
                env=dict(os.environ),
                timeout=10,
                streaming=True,
                resume_session_id="",
                prompt="hello",
                agent_cwd=str(tmp_path),
            )
        ]

    events = asyncio.run(collect())

    delta = next(event for event in events if event["type"] == "message.delta")
    assert len(delta["data"]["chunk"]["content"]) == 70000
    assert events[-1]["type"] == "run.completed"


def test_codex_remote_mcp_config_is_not_embedded_in_ssh_command() -> None:
    module = _load_plugin_module("codex-agent", "pkg/native_cli.py", "native_cli_remote")
    mcp_toml = module._mcp_config_toml(
        {
            "langbot": {
                "type": "http",
                "url": "http://127.0.0.1:1234/mcp",
                "headers": {"Authorization": "Bearer remote-secret-token"},
            }
        }
    )

    command = module._remote_shell_command(
        "/workspace",
        ["codex", "app-server", "--listen", "stdio://"],
        {},
        home_key="thread-123",
        read_mcp_from_stdin=True,
    )

    assert "remote-secret-token" not in command
    assert "Authorization" not in command
    assert b"remote-secret-token" not in module._remote_mcp_stdin_prelude(mcp_toml)


def test_native_cli_startup_failures_are_run_failed(tmp_path: Path) -> None:
    claude_module = _load_runner_module("claude-code-agent")
    claude_runner = object.__new__(claude_module.DefaultAgentRunner)
    claude_runner.get_plugin_config = lambda: {}
    claude_runner.get_run_api = lambda ctx: None
    claude_results = asyncio.run(
        _collect_async(
            claude_runner.run(
                _native_ctx(
                    {
                        "location": "local",
                        "command": str(tmp_path / "missing-claude"),
                        "workspace": str(tmp_path),
                        "langbot-assets-enabled": False,
                    }
                )
            )
        )
    )

    assert [item.type for item in claude_results] == ["state.updated", "run.failed"]
    assert claude_results[-1].data["code"] == "claude_code.command_not_found"

    codex_module = _load_runner_module("codex-agent")
    codex_runner = object.__new__(codex_module.DefaultAgentRunner)
    codex_runner.get_plugin_config = lambda: {}
    codex_runner.get_run_api = lambda ctx: None
    codex_results = asyncio.run(
        _collect_async(
            codex_runner.run(
                _native_ctx(
                    {
                        "location": "local",
                        "command": str(tmp_path / "missing-codex"),
                        "workspace": str(tmp_path),
                        "langbot-assets-enabled": False,
                    }
                )
            )
        )
    )

    assert [item.type for item in codex_results] == ["run.failed"]
    assert codex_results[0].data["code"] == "codex.command_not_found"


def test_claude_code_runner_redacts_stderr_secrets(tmp_path: Path) -> None:
    fake_cli = tmp_path / "fake_secret_stderr.py"
    fake_cli.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import sys",
                "sys.stdin.read()",
                "print('Authorization: Bearer stderr-secret-token run_token=run-secret-value', file=sys.stderr)",
                "raise SystemExit(7)",
            ]
        ),
        encoding="utf-8",
    )
    module = _load_runner_module("claude-code-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    runner.get_plugin_config = lambda: {}
    runner.get_run_api = lambda ctx: None

    results = asyncio.run(
        _collect_async(
            runner.run(
                _native_ctx(
                    {
                        "location": "local",
                        "command": sys.executable,
                        "args-json": [str(fake_cli)],
                        "workspace": str(tmp_path),
                        "langbot-assets-enabled": False,
                    }
                )
            )
        )
    )

    assert [item.type for item in results] == ["state.updated", "run.failed"]
    error = results[-1].data["error"]
    assert "stderr-secret-token" not in error
    assert "run-secret-value" not in error
    assert "[REDACTED]" in error


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

    prompt = object.__new__(module.DefaultAgentRunner)._with_run_scope_prompt(ctx, "call langbot_get_current_event")
    assert "Call LangBot MCP tools directly in this ACP session" in prompt
    assert "do not launch background agents, subagents, or tasks" in prompt
    assert "Wait for each LangBot MCP tool result before replying" in prompt


def test_external_service_runners_declare_minimal_plugin_storage_permission() -> None:
    # Asset-callback runners legitimately request broader permissions (tools,
    # knowledge bases, history) for the LangBot Asset Gateway MCP; the rest only
    # need plugin storage.
    asset_callback_runners = {
        "acp-agent-runner",
        "claude-code-agent",
        "codex-agent",
        "dify-agent",
        "n8n-agent",
        "coze-agent",
        "dashscope-agent",
        "langflow-agent",
    }
    for plugin_dir in PLUGIN_DIRS - asset_callback_runners:
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
