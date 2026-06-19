from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import yaml
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentEventContext,
    AgentInput,
    AgentResources,
    AgentRunContext,
    AgentRunState,
    AgentRuntimeContext,
    AgentTrigger,
    ConversationContext,
    DeliveryContext,
)
from langbot_plugin.api.entities.builtin.provider.message import ContentElement

ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_runner_module(plugin_dir: str, stubs: dict[str, Any] | None = None):
    for module_name in list(sys.modules):
        if module_name == "pkg" or module_name.startswith("pkg."):
            del sys.modules[module_name]

    installed_stubs: dict[str, Any] = {}
    for name, value in (stubs or {}).items():
        if name not in sys.modules:
            installed_stubs[name] = value
            sys.modules[name] = value

    plugin_root = ROOT / plugin_dir
    sys.path.insert(0, str(plugin_root))
    try:
        module_path = plugin_root / "components" / "agent_runner" / "default.py"
        spec = importlib.util.spec_from_file_location(
            f"test_traditional_{plugin_dir.replace('-', '_')}_runner",
            module_path,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(plugin_root))
        for name in installed_stubs:
            sys.modules.pop(name, None)


async def _collect_async(generator):
    return [item async for item in generator]


def _type(result) -> str:
    return getattr(result.type, "value", str(result.type))


def _ctx(
    *,
    config: dict[str, Any] | None = None,
    text: str = "hello",
    contents: list[ContentElement] | None = None,
    conversation_state: dict[str, Any] | None = None,
) -> AgentRunContext:
    return AgentRunContext(
        run_id="run_traditional",
        trigger=AgentTrigger(type="message.received"),
        event=AgentEventContext(event_id="evt_1", event_type="message.received", source="test"),
        conversation=ConversationContext(
            conversation_id="langbot-conv-1",
            session_id="langbot-session-1",
        ),
        input=AgentInput(text=text, contents=contents or []),
        delivery=DeliveryContext(surface="test", supports_streaming=True),
        resources=AgentResources(),
        state=AgentRunState(conversation=conversation_state or {}),
        runtime=AgentRuntimeContext(),
        config=config or {},
    )


def _text_and_image_ctx(config: dict[str, Any]) -> AgentRunContext:
    return _ctx(
        config=config,
        text="describe this",
        contents=[ContentElement.from_image_base64("data:image/png;base64,aGVsbG8=")],
    )


def test_traditional_requirements_match_direct_imports() -> None:
    assert "httpx" in (ROOT / "dify-agent" / "requirements.txt").read_text(encoding="utf-8")
    assert "aiohttp" in (ROOT / "coze-agent" / "requirements.txt").read_text(encoding="utf-8")
    assert "dashscope" in (ROOT / "dashscope-agent" / "requirements.txt").read_text(encoding="utf-8")
    assert "httpx" in (ROOT / "n8n-agent" / "requirements.txt").read_text(encoding="utf-8")
    assert "httpx" in (ROOT / "langflow-agent" / "requirements.txt").read_text(encoding="utf-8")
    assert "aiohttp" not in (ROOT / "tbox-agent" / "requirements.txt").read_text(encoding="utf-8")


def test_coze_does_not_reuse_langbot_conversation_id_as_external_id() -> None:
    module = _load_runner_module("coze-agent")
    runner = object.__new__(module.DefaultAgentRunner)

    assert runner._get_external_conversation_id(_ctx()) is None

    ctx = _ctx(conversation_state={"external.conversation_id": "coze-conv-1"})
    assert runner._get_external_conversation_id(ctx) == "coze-conv-1"


def test_n8n_uses_runner_owned_conversation_and_session_ids() -> None:
    module = _load_runner_module("n8n-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    ctx = _ctx()

    conversation_id, conversation_created = runner._get_or_create_state_id(
        ctx,
        "external.conversation_id",
        "n8n_conversation",
    )
    session_id, session_created = runner._get_or_create_state_id(
        ctx,
        "external.session_id",
        "n8n_session",
    )
    payload = runner._build_payload(ctx, "hello", conversation_id, session_id)

    assert conversation_created is True
    assert session_created is True
    assert payload["conversation_id"].startswith("n8n_conversation_")
    assert payload["session_id"].startswith("n8n_session_")
    assert payload["conversation_id"] != "langbot-conv-1"
    assert payload["session_id"] != "langbot-session-1"


def test_dify_workflow_uses_runner_owned_state_ids() -> None:
    module = _load_runner_module("dify-agent")
    runner = object.__new__(module.DefaultAgentRunner)
    captured_inputs: dict[str, Any] = {}

    class FakeClient:
        async def workflow_run(self, *, inputs, user, files):
            captured_inputs.update(inputs)
            yield {
                "event": "workflow_finished",
                "data": {"outputs": {"summary": "workflow ok"}},
            }

    ctx = _ctx()
    results = asyncio.run(
        _collect_async(
            runner._run_workflow(
                ctx,
                FakeClient(),
                {},
                "hello",
                "user_1",
                [],
                False,
            )
        )
    )

    assert captured_inputs["langbot_session_id"].startswith("dify_session_")
    assert captured_inputs["langbot_conversation_id"].startswith("dify_conversation_")
    assert captured_inputs["langbot_session_id"] != "langbot-session-1"
    assert captured_inputs["langbot_conversation_id"] != "langbot-conv-1"
    assert ("state.updated", "external.workflow_session_id") in {
        (_type(item), item.data.get("key")) for item in results
    }
    assert ("state.updated", "external.workflow_conversation_id") in {
        (_type(item), item.data.get("key")) for item in results
    }
    assert _type(results[-1]) == "run.completed"


def test_dify_text_image_upload_failure_is_input_error() -> None:
    module = _load_runner_module("dify-agent")

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def upload_file(self, *args, **kwargs):
            raise module.DifyAPIError("provider upload failed", code="dify.http_error")

    module.AsyncDifyClient = FakeClient
    runner = object.__new__(module.DefaultAgentRunner)
    ctx = _text_and_image_ctx(
        {
            "base-url": "https://api.dify.ai/v1",
            "api-key": "key",
            "app-type": "chat",
        }
    )

    results = asyncio.run(_collect_async(runner.run(ctx)))

    assert [_type(item) for item in results] == ["run.failed"]
    assert results[0].data["code"] == "dify.input_error"
    assert results[0].data["retryable"] is False


def test_coze_text_image_upload_failure_is_input_error() -> None:
    module = _load_runner_module("coze-agent")

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def upload_file(self, *args, **kwargs):
            raise module.CozeAPIError("provider upload failed", code="coze.http_error")

        async def close(self):
            pass

    module.AsyncCozeClient = FakeClient
    runner = object.__new__(module.DefaultAgentRunner)
    ctx = _text_and_image_ctx({"api-key": "key", "bot-id": "bot"})

    results = asyncio.run(_collect_async(runner.run(ctx)))

    assert [_type(item) for item in results] == ["run.failed"]
    assert results[0].data["code"] == "coze.input_error"


def test_dashscope_timeout_maps_to_retryable_run_failed() -> None:
    dashscope_stub = types.SimpleNamespace(Application=types.SimpleNamespace(call=lambda **kwargs: iter(())))
    module = _load_runner_module("dashscope-agent", stubs={"dashscope": dashscope_stub})

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def iter_agent(self, **kwargs):
            raise module.DashScopeAPIError("timeout", code="dashscope.timeout", retryable=True)
            yield {}

    module.DashScopeClient = FakeClient
    runner = object.__new__(module.DefaultAgentRunner)
    ctx = _ctx(config={"api-key": "key", "app-id": "app", "app-type": "agent"})

    results = asyncio.run(_collect_async(runner.run(ctx)))

    assert [_type(item) for item in results] == ["run.failed"]
    assert results[0].data == {"code": "dashscope.timeout", "error": "timeout", "retryable": True}


def test_dashscope_empty_output_fails_instead_of_completing() -> None:
    dashscope_stub = types.SimpleNamespace(Application=types.SimpleNamespace(call=lambda **kwargs: iter(())))
    module = _load_runner_module("dashscope-agent", stubs={"dashscope": dashscope_stub})

    class FakeClient:
        references_quote = "refs:"

        def __init__(self, **kwargs):
            pass

        async def iter_agent(self, **kwargs):
            if False:
                yield {}

    module.DashScopeClient = FakeClient
    runner = object.__new__(module.DefaultAgentRunner)
    ctx = _ctx(config={"api-key": "key", "app-id": "app", "app-type": "agent"})

    results = asyncio.run(_collect_async(runner.run(ctx)))

    assert [_type(item) for item in results] == ["run.failed"]
    assert results[0].data["code"] == "dashscope.empty_response"


def test_tbox_timeout_maps_to_retryable_run_failed() -> None:
    module = _load_runner_module("tbox-agent")

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def chat(self, **kwargs):
            raise module.TboxAPIError("timeout", code="tbox.timeout", retryable=True)
            yield {}

    module.AsyncTboxClient = FakeClient
    runner = object.__new__(module.DefaultAgentRunner)
    ctx = _ctx(config={"api-key": "key", "app-id": "app"})

    results = asyncio.run(_collect_async(runner.run(ctx)))

    assert [_type(item) for item in results] == ["run.failed"]
    assert results[0].data == {"code": "tbox.timeout", "error": "timeout", "retryable": True}


def test_deerflow_timeout_maps_to_retryable_run_failed() -> None:
    module = _load_runner_module("deerflow-agent")

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def stream_run(self, **kwargs):
            raise TimeoutError("provider timeout")
            yield {}

    module.AsyncDeerFlowClient = FakeClient
    runner = object.__new__(module.DefaultAgentRunner)
    runner._ensure_thread_id = lambda ctx, client, timeout: _async_value(("thread-1", False))
    ctx = _ctx(config={"api-base": "http://127.0.0.1:2026", "streaming": True, "timeout": 1})

    results = asyncio.run(_collect_async(runner.run(ctx)))

    assert [_type(item) for item in results] == ["run.failed"]
    assert results[0].data["code"] == "deerflow.timeout"
    assert results[0].data["retryable"] is True


async def _async_value(value):
    return value


def test_weknora_timeout_maps_to_retryable_run_failed() -> None:
    module = _load_runner_module("weknora-agent")

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def create_session(self, **kwargs):
            raise module.WeKnoraAPIError("timeout", code="weknora.timeout", retryable=True)

    module.AsyncWeKnoraClient = FakeClient
    runner = object.__new__(module.DefaultAgentRunner)
    ctx = _ctx(config={"base-url": "http://weknora/api/v1", "api-key": "key"})

    results = asyncio.run(_collect_async(runner.run(ctx)))

    assert [_type(item) for item in results] == ["run.failed"]
    assert results[0].data == {"code": "weknora.timeout", "error": "timeout", "retryable": True}


def test_weknora_empty_answer_fails_instead_of_completing() -> None:
    module = _load_runner_module("weknora-agent")

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def agent_chat(self, **kwargs) -> AsyncGenerator[dict[str, Any], None]:
            yield {"response_type": "answer", "content": "", "done": True}

    module.AsyncWeKnoraClient = FakeClient
    runner = object.__new__(module.DefaultAgentRunner)
    ctx = _ctx(
        config={"base-url": "http://weknora/api/v1", "api-key": "key", "app-type": "agent"},
        conversation_state={"external.session_id": "weknora-session-1"},
    )

    results = asyncio.run(_collect_async(runner.run(ctx)))

    assert [_type(item) for item in results] == ["run.failed"]
    assert results[0].data["code"] == "weknora.empty_response"


def test_weknora_manifest_does_not_advertise_host_tool_or_knowledge_capabilities() -> None:
    runner = _load_yaml(ROOT / "weknora-agent" / "components" / "agent_runner" / "default.yaml")

    assert runner["spec"]["capabilities"]["tool_calling"] is False
    assert runner["spec"]["capabilities"]["knowledge_retrieval"] is False
    assert runner["spec"]["permissions"] == {"storage": ["plugin"]}
