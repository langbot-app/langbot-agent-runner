from __future__ import annotations

import asyncio
import importlib.util
import sys
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
        assert runner["spec"]["protocol_version"] == "1"
        assert runner["execution"]["python"]["path"] == "default.py"
        assert runner["execution"]["python"]["attr"] == "DefaultAgentRunner"


def test_readme_lists_code_runner_ids() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "`claude-code-agent`" in readme
    assert "`plugin:langbot/claude-code-agent/default`" in readme
    assert "`codex-agent`" in readme
    assert "`plugin:langbot/codex-agent/default`" in readme


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


def test_dify_runner_uses_protocol_v1_actor_fields_for_user_tag() -> None:
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

    module = _load_runner_module("dify-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    ctx = AgentRunContext(
        run_id="run_1",
        trigger=AgentTrigger(type="message.received"),
        event=AgentEventContext(
            event_id="evt_1",
            event_type="message.received",
            source="pipeline_adapter",
        ),
        input=AgentInput(text="hello"),
        delivery=DeliveryContext(surface="pipeline"),
        resources=AgentResources(),
        runtime=AgentRuntimeContext(),
        actor=ActorContext(actor_type="user", actor_id="user_1"),
    )

    assert runner._get_user_tag(ctx) == "user_user_1"


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
                source="pipeline_adapter",
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

    return AgentRunContext(
        run_id="run_1",
        trigger=AgentTrigger(type="message.received"),
        event=AgentEventContext(
            event_id="evt_1",
            event_type="message.received",
            source="pipeline_adapter",
        ),
        input=AgentInput(text=text),
        delivery=DeliveryContext(surface="pipeline"),
        resources=AgentResources(),
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


def test_claude_code_runner_dry_run_returns_mock_response() -> None:
    module = _load_runner_module("claude-code-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    ctx = _agent_run_context(
        text="write a test",
        config={
            "dry-run": True,
            "mock-response": "mocked",
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert [result.type.value for result in results] == ["message.completed", "run.completed"]
    assert results[0].data["message"]["content"] == "mocked"


def test_claude_code_runner_invokes_configured_cli(monkeypatch) -> None:
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
        text="summarize this",
        config={
            "cli-command": "claude",
            "working-directory": "/tmp",
            "inject-context": False,
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
        "--max-turns",
        "1",
        "--permission-mode",
        "plan",
        "--disallowedTools",
        "AskUserQuestion",
        "--resume",
        "sess_existing",
    )
    assert captured["stdin"] == b"summarize this"
    assert captured["kwargs"]["stdin"] is module.asyncio.subprocess.PIPE
    assert captured["kwargs"]["cwd"] == "/tmp"
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
        "value": "/tmp",
    }


def test_claude_code_runner_supports_stream_json_protocol(monkeypatch) -> None:
    module = _load_runner_module("claude-code-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, stdin):
            captured["stdin"] = stdin
            stdout = "\n".join(
                [
                    '{"type":"system","session_id":"sess_stream"}',
                    '{"type":"assistant","message":{"content":[{"type":"text","text":"partial"}]}}',
                    '{"type":"result","session_id":"sess_stream","result":"final"}',
                ]
            )
            return stdout.encode("utf-8"), b""

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
            "inject-context": False,
            "input-format": "stream-json",
            "output-format": "stream-json",
            "setting-sources": "local",
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert captured["command"] == (
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--input-format",
        "stream-json",
        "--verbose",
        "--setting-sources",
        "local",
        "--max-turns",
        "1",
        "--permission-mode",
        "plan",
        "--disallowedTools",
        "AskUserQuestion",
        "--resume",
        "sess_existing",
    )
    assert b'"type": "user"' in captured["stdin"]
    assert b"summarize this" in captured["stdin"]
    assert captured["kwargs"]["stdin"] is module.asyncio.subprocess.PIPE
    assert captured["kwargs"]["cwd"] == "/tmp"
    assert [result.type.value for result in results] == [
        "message.completed",
        "state.updated",
        "state.updated",
        "run.completed",
    ]
    assert results[0].data["message"]["content"] == "final"
    assert results[1].data["value"] == "sess_stream"
    assert results[2].data["value"] == "/tmp"


def test_claude_code_runner_injects_context_skills_and_mcp_config(monkeypatch, tmp_path) -> None:
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
            "cli-command": "claude",
            "working-directory": str(tmp_path),
            "context-directory": ".langbot/test-agent-runner",
            "input-format": "stream-json",
            "output-format": "stream-json",
            "skills-json": {
                "skills": [
                    {
                        "name": "langbot-support",
                        "content": "# LangBot Support\nUse scoped resources only.",
                        "files": {"references/checklist.md": "check resources"},
                    }
                ]
            },
            "mcp-config-json": {
                "mcpServers": {
                    "langbot": {
                        "command": "langbot-mcp",
                        "args": ["serve"],
                    }
                }
            },
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    run_dir = tmp_path / ".langbot" / "test-agent-runner" / "run_1"
    context_json = run_dir / "agent-context.json"
    context_markdown = run_dir / "LANGBOT_CONTEXT.md"
    mcp_config = run_dir / "mcp.json"
    skill_file = tmp_path / ".claude" / "skills" / "langbot-support" / "SKILL.md"
    skill_reference = tmp_path / ".claude" / "skills" / "langbot-support" / "references" / "checklist.md"

    assert captured["command"] == (
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--input-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        "1",
        "--permission-mode",
        "plan",
        "--disallowedTools",
        "AskUserQuestion",
        "--mcp-config",
        str(mcp_config),
        "--strict-mcp-config",
        "--resume",
        "sess_existing",
    )
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert b"LangBot prepared read-only run context" in captured["stdin"]
    assert str(context_json).encode("utf-8") in captured["stdin"]
    assert b"use langbot context" in captured["stdin"]

    assert context_json.exists()
    assert context_markdown.exists()
    assert mcp_config.exists()
    assert skill_file.read_text(encoding="utf-8") == "# LangBot Support\nUse scoped resources only."
    assert skill_reference.read_text(encoding="utf-8") == "check resources"

    context_payload = module.json.loads(context_json.read_text(encoding="utf-8"))
    assert context_payload["schema"] == "langbot.agent_runner.external_harness_context.v1"
    assert context_payload["event"]["event_type"] == "message.received"
    assert context_payload["input"]["text"] == "use langbot context"
    assert module.json.loads(mcp_config.read_text(encoding="utf-8"))["mcpServers"]["langbot"]["command"] == "langbot-mcp"
    assert [result.type.value for result in results] == [
        "message.completed",
        "state.updated",
        "state.updated",
        "run.completed",
    ]
    assert results[-1].data["external"]["provider"] == "claude_code"
    assert results[-1].data["external"]["session_id"] == "sess_new"


def test_claude_code_runner_command_not_found_is_structured(monkeypatch) -> None:
    module = _load_runner_module("claude-code-agent")
    runner = object.__new__(module.DefaultAgentRunner)

    async def fake_create_subprocess_exec(*command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    ctx = _agent_run_context(
        text="hello",
        config={
            "cli-command": "missing-claude",
            "working-directory": "/tmp",
            "inject-context": False,
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert [result.type.value for result in results] == ["run.failed"]
    assert results[0].data["code"] == "claude_code.command_not_found"
    assert "missing-claude" in results[0].data["error"]


def test_claude_code_runner_nonzero_exit_is_structured(monkeypatch) -> None:
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
            "working-directory": "/tmp",
            "inject-context": False,
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert [result.type.value for result in results] == ["run.failed"]
    assert results[0].data["code"] == "claude_code.cli_error"
    assert results[0].data["error"] == "bad claude config"


def test_claude_code_runner_timeout_is_structured(monkeypatch) -> None:
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
            "working-directory": "/tmp",
            "inject-context": False,
            "timeout": 0.01,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert [result.type.value for result in results] == ["run.failed"]
    assert results[0].data["code"] == "claude_code.timeout"
    assert results[0].data["retryable"] is True
    assert captured == {"killed": True, "waited": True}


def test_codex_runner_dry_run_returns_mock_response() -> None:
    module = _load_runner_module("codex-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    ctx = _agent_run_context(
        text="write a test",
        config={
            "dry-run": True,
            "mock-response": "mocked codex",
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert [result.type.value for result in results] == ["message.completed", "run.completed"]
    assert results[0].data["message"]["content"] == "mocked codex"


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
        "sess_existing",
        "-",
    )
    assert captured["stdin"] == b"continue"
    assert results[0].data["message"]["content"] == "resumed output"
    assert results[1].data["value"] == "thread_resumed"


def test_codex_runner_injects_context_skills_and_mcp_config(monkeypatch, tmp_path) -> None:
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
            "skills-json": {
                "skills": [
                    {
                        "name": "langbot-support",
                        "content": "# LangBot Support\nUse scoped resources only.",
                        "files": {"references/checklist.md": "check resources"},
                    }
                ]
            },
            "mcp-config-json": {
                "mcpServers": {
                    "langbot-agent": {
                        "command": "langbot-mcp",
                        "args": ["serve"],
                    }
                }
            },
            "environment-json": {"HTTP_PROXY": "http://127.0.0.1:7890"},
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    run_dir = tmp_path / ".langbot" / "test-agent-runner" / "run_1"
    context_json = run_dir / "agent-context.json"
    context_markdown = run_dir / "LANGBOT_CONTEXT.md"
    mcp_config = run_dir / "mcp.json"
    skill_file = run_dir / "codex-skills" / "langbot-support" / "SKILL.md"
    skill_reference = run_dir / "codex-skills" / "langbot-support" / "references" / "checklist.md"

    assert "--config" in captured["command"]
    assert 'mcp_servers.langbot_agent.command="langbot-mcp"' in captured["command"]
    assert 'mcp_servers.langbot_agent.args=["serve"]' in captured["command"]
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["kwargs"]["env"]["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert b"LangBot prepared read-only run context" in captured["stdin"]
    assert str(context_json).encode("utf-8") in captured["stdin"]
    assert b"use langbot context" in captured["stdin"]

    assert context_json.exists()
    assert context_markdown.exists()
    assert mcp_config.exists()
    assert (run_dir / "codex-events.jsonl").read_text(encoding="utf-8") == '{"type":"thread.started","thread_id":"thread_new"}\n'
    assert skill_file.read_text(encoding="utf-8") == "# LangBot Support\nUse scoped resources only."
    assert skill_reference.read_text(encoding="utf-8") == "check resources"

    context_payload = module.json.loads(context_json.read_text(encoding="utf-8"))
    assert context_payload["schema"] == "langbot.agent_runner.external_harness_context.v1"
    assert context_payload["event"]["event_type"] == "message.received"
    assert context_payload["input"]["text"] == "use langbot context"
    assert module.json.loads(mcp_config.read_text(encoding="utf-8"))["mcpServers"]["langbot-agent"]["command"] == "langbot-mcp"
    assert [result.type.value for result in results] == [
        "message.completed",
        "state.updated",
        "state.updated",
        "run.completed",
    ]
    assert results[-1].data["external"]["provider"] == "codex"


def test_codex_runner_command_not_found_is_structured(monkeypatch) -> None:
    module = _load_runner_module("codex-agent")
    runner = object.__new__(module.DefaultAgentRunner)

    async def fake_create_subprocess_exec(*command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    ctx = _agent_run_context(
        text="hello",
        config={
            "cli-command": "missing-codex",
            "working-directory": "/tmp",
            "inject-context": False,
            "resume": False,
            "timeout": 15,
        },
    )

    results = asyncio.run(_collect_results(runner, ctx))

    assert [result.type.value for result in results] == ["run.failed"]
    assert results[0].data["code"] == "codex.command_not_found"
    assert "missing-codex" in results[0].data["error"]
