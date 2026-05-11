"""Tests for Dify AgentRunner implementation.

Uses httpx mocking to simulate Dify API responses.
"""

from __future__ import annotations

import json
import pathlib
import typing

import httpx
import pytest
import respx
from langbot_agent_runner_utils.dify_client import (
    AsyncDifyClient,
    DifyAPIError,
)
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentInput,
    AgentResources,
    AgentRunContext,
    AgentRunState,
    AgentRuntimeContext,
    AgentTrigger,
    ConversationContext,
)


def create_test_context(
    text: str = "Hello",
    config: dict[str, typing.Any] = None,
    attachments: list = None,
    params: dict[str, typing.Any] = None,
    state: AgentRunState = None,
    conversation: ConversationContext = None,
) -> AgentRunContext:
    """Create a test AgentRunContext with optional params/state/conversation."""
    return AgentRunContext(
        run_id="test_run_123",
        trigger=AgentTrigger(type="message.received"),
        input=AgentInput(text=text, attachments=attachments or []),
        resources=AgentResources(),
        runtime=AgentRuntimeContext(),
        config=config or {},
        params=params or {},
        state=state or AgentRunState(),
        conversation=conversation,
    )


class TestDifyClient:
    """Tests for AsyncDifyClient."""

    @pytest.mark.anyio
    async def test_chat_messages_success(self):
        """Test successful chat-messages streaming."""
        base_url = "https://api.dify.ai/v1"
        api_key = "test-key"

        # Mock SSE response
        events = [
            {"event": "message", "answer": "Hello"},
            {"event": "message", "answer": " world"},
            {"event": "message_end", "conversation_id": "conv_123"},
        ]
        sse_lines = "".join([f"data: {json.dumps(e)}\n\n" for e in events])

        with respx.mock(base_url=base_url) as mock:
            mock.post("/chat-messages").mock(
                return_value=httpx.Response(200, text=sse_lines)
            )

            client = AsyncDifyClient(api_key=api_key, base_url=base_url)
            results = []
            async for event in client.chat_messages(
                inputs={}, query="Hi", user="user_123"
            ):
                results.append(event)

            assert len(results) == 3
            assert results[0]["event"] == "message"
            assert results[0]["answer"] == "Hello"
            assert results[2]["event"] == "message_end"

    @pytest.mark.anyio
    async def test_chat_messages_http_error(self):
        """Test chat-messages HTTP error."""
        base_url = "https://api.dify.ai/v1"
        api_key = "test-key"

        with respx.mock(base_url=base_url) as mock:
            mock.post("/chat-messages").mock(
                return_value=httpx.Response(401, text="Unauthorized")
            )

            client = AsyncDifyClient(api_key=api_key, base_url=base_url)

            with pytest.raises(DifyAPIError) as exc_info:
                async for _ in client.chat_messages(inputs={}, query="Hi", user="user_123"):
                    pass

            assert exc_info.value.code == "dify.http_error"

    @pytest.mark.anyio
    async def test_chat_messages_timeout(self):
        """Test chat-messages timeout."""
        base_url = "https://api.dify.ai/v1"
        api_key = "test-key"

        with respx.mock(base_url=base_url) as mock:
            # Simulate timeout by not responding
            route = mock.post("/chat-messages")
            route.side_effect = httpx.TimeoutException("Timeout")

            client = AsyncDifyClient(api_key=api_key, base_url=base_url, timeout=1.0)

            with pytest.raises(DifyAPIError) as exc_info:
                async for _ in client.chat_messages(inputs={}, query="Hi", user="user_123"):
                    pass

            assert exc_info.value.code == "dify.timeout"

    @pytest.mark.anyio
    async def test_workflow_run_success(self):
        """Test successful workflow-run streaming."""
        base_url = "https://api.dify.ai/v1"
        api_key = "test-key"

        events = [
            {"event": "workflow_started"},
            {"event": "node_started", "data": {"node_id": "n1", "node_type": "start", "title": "Start"}},
            {"event": "text_chunk", "data": {"text": "Hello"}},
            {"event": "workflow_finished", "data": {"outputs": {"summary": "Done"}, "error": None}},
        ]
        sse_lines = "".join([f"data: {json.dumps(e)}\n\n" for e in events])

        with respx.mock(base_url=base_url) as mock:
            mock.post("/workflows/run").mock(
                return_value=httpx.Response(200, text=sse_lines)
            )

            client = AsyncDifyClient(api_key=api_key, base_url=base_url)
            results = []
            async for event in client.workflow_run(inputs={}, user="user_123"):
                results.append(event)

            assert len(results) == 4
            assert results[3]["event"] == "workflow_finished"

    @pytest.mark.anyio
    async def test_invalid_response_format(self):
        """Test invalid SSE data handling."""
        base_url = "https://api.dify.ai/v1"
        api_key = "test-key"

        # Invalid JSON in SSE
        sse_lines = "data: {invalid json}\n\n"

        with respx.mock(base_url=base_url) as mock:
            mock.post("/chat-messages").mock(
                return_value=httpx.Response(200, text=sse_lines)
            )

            client = AsyncDifyClient(api_key=api_key, base_url=base_url)

            with pytest.raises(DifyAPIError) as exc_info:
                async for _ in client.chat_messages(inputs={}, query="Hi", user="user_123"):
                    pass

            assert exc_info.value.code == "dify.response_invalid"


class TestDefaultAgentRunner:
    """Tests for DefaultAgentRunner."""

    @pytest.fixture
    def runner(self):
        """Create runner instance."""
        import importlib.util
        import os
        import sys

        # Load the runner directly from file path (same approach as contract tests)
        REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
        plugin_dir = REPO_ROOT / "dify-agent"
        runner_path = plugin_dir / "components" / "agent_runner" / "default.py"

        # Add plugin dir to sys.path for imports within default.py
        original_path = sys.path.copy()
        original_cwd = os.getcwd()
        try:
            os.chdir(plugin_dir)
            sys.path.insert(0, str(plugin_dir))

            # Load module from file path
            spec = importlib.util.spec_from_file_location("default", str(runner_path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            return module.DefaultAgentRunner()
        finally:
            os.chdir(original_cwd)
            sys.path[:] = original_path

    @pytest.mark.anyio
    async def test_config_missing_api_key(self, runner):
        """Test missing api-key returns run.failed."""
        ctx = create_test_context(
            text="Hello",
            config={"base-url": "https://api.dify.ai/v1", "app-type": "chat"}
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        assert len(results) == 1
        assert results[0].type.value == "run.failed"
        assert results[0].data["code"] == "dify.config_invalid"

    @pytest.mark.anyio
    async def test_config_missing_base_url(self, runner):
        """Test missing base-url returns run.failed."""
        ctx = create_test_context(
            text="Hello",
            config={"base-url": "", "app-type": "chat", "api-key": "key"}
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        # Empty base-url triggers error
        assert len(results) == 1
        assert results[0].type.value == "run.failed"

    @pytest.mark.anyio
    async def test_config_invalid_app_type(self, runner):
        """Test invalid app-type returns run.failed."""
        ctx = create_test_context(
            text="Hello",
            config={"base-url": "https://api.dify.ai/v1", "app-type": "invalid", "api-key": "key"}
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        assert len(results) == 1
        assert results[0].type.value == "run.failed"
        assert results[0].data["code"] == "dify.config_invalid"

    @pytest.mark.anyio
    @respx.mock(base_url="https://api.dify.ai/v1")
    async def test_chat_streaming_success(self, runner, respx_mock):
        """Test chat mode streaming success."""
        events = [
            {"event": "message", "answer": "Hello"},
            {"event": "message", "answer": " there"},
            {"event": "message_end", "conversation_id": "conv_123"},
        ]
        sse_lines = "".join([f"data: {json.dumps(e)}\n\n" for e in events])

        respx_mock.post("/chat-messages").mock(
            return_value=httpx.Response(200, text=sse_lines)
        )

        ctx = create_test_context(
            text="Hi",
            config={
                "base-url": "https://api.dify.ai/v1",
                "app-type": "chat",
                "api-key": "test-key",
                "timeout": 30,
            }
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        # Should have message_delta and run_completed
        assert len(results) >= 2
        # Last should be run.completed
        assert results[-1].type.value == "run.completed"
        # Should have delta with content
        delta_found = False
        for r in results:
            if r.type.value == "message.delta":
                delta_found = True
                break
        assert delta_found

    @pytest.mark.anyio
    @respx.mock(base_url="https://api.dify.ai/v1")
    async def test_agent_streaming_success(self, runner, respx_mock):
        """Test agent mode streaming success."""
        events = [
            {"event": "agent_message", "answer": "Thinking"},
            {"event": "agent_message", "answer": "..."},
            {"event": "agent_thought", "id": "t1", "tool": "search", "observation": ""},
            {"event": "agent_message", "answer": "Done"},
            {"event": "message_end", "conversation_id": "conv_456"},
        ]
        sse_lines = "".join([f"data: {json.dumps(e)}\n\n" for e in events])

        respx_mock.post("/chat-messages").mock(
            return_value=httpx.Response(200, text=sse_lines)
        )

        ctx = create_test_context(
            text="Hi",
            config={
                "base-url": "https://api.dify.ai/v1",
                "app-type": "agent",
                "api-key": "test-key",
            }
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        assert len(results) >= 2
        assert results[-1].type.value == "run.completed"

    @pytest.mark.anyio
    @respx.mock(base_url="https://api.dify.ai/v1")
    async def test_workflow_streaming_success(self, runner, respx_mock):
        """Test workflow mode streaming success."""
        events = [
            {"event": "workflow_started"},
            {"event": "node_started", "data": {"node_id": "n1", "node_type": "llm", "title": "LLM"}},
            {"event": "node_finished", "data": {"node_id": "n1", "node_type": "llm"}},
            {"event": "workflow_finished", "data": {"outputs": {"summary": "Workflow result"}, "error": None}},
        ]
        sse_lines = "".join([f"data: {json.dumps(e)}\n\n" for e in events])

        respx_mock.post("/workflows/run").mock(
            return_value=httpx.Response(200, text=sse_lines)
        )

        ctx = create_test_context(
            text="Run workflow",
            config={
                "base-url": "https://api.dify.ai/v1",
                "app-type": "workflow",
                "api-key": "test-key",
            }
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        assert len(results) >= 2
        assert results[-1].type.value == "run.completed"

    @pytest.mark.anyio
    @respx.mock(base_url="https://api.dify.ai/v1")
    async def test_workflow_text_chunk_streaming(self, runner, respx_mock):
        """Test workflow streaming text_chunk without summary."""
        events = [
            {"event": "workflow_started"},
            {"event": "text_chunk", "data": {"text": "Hello"}},
            {"event": "text_chunk", "data": {"text": " world"}},
            {"event": "workflow_finished", "data": {"outputs": {}, "error": None}},
        ]
        sse_lines = "".join([f"data: {json.dumps(e)}\n\n" for e in events])

        respx_mock.post("/workflows/run").mock(
            return_value=httpx.Response(200, text=sse_lines)
        )

        ctx = create_test_context(
            text="Run workflow",
            config={
                "base-url": "https://api.dify.ai/v1",
                "app-type": "workflow",
                "api-key": "test-key",
            }
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        # Should have message_delta with text_chunk content
        assert len(results) >= 2
        assert results[-1].type.value == "run.completed"

        # Check that text_chunk content is in results
        delta_found = False
        for r in results:
            if r.type.value == "message.delta":
                delta_found = True
                break
        assert delta_found

    @pytest.mark.anyio
    @respx.mock(base_url="https://api.dify.ai/v1")
    async def test_http_error_failure(self, runner, respx_mock):
        """Test HTTP error returns run.failed."""
        respx_mock.post("/chat-messages").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        ctx = create_test_context(
            text="Hi",
            config={
                "base-url": "https://api.dify.ai/v1",
                "app-type": "chat",
                "api-key": "test-key",
            }
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        assert len(results) == 1
        assert results[0].type.value == "run.failed"
        assert results[0].data["code"] == "dify.http_error"

    @pytest.mark.anyio
    @respx.mock(base_url="https://api.dify.ai/v1")
    async def test_timeout_failure(self, runner, respx_mock):
        """Test timeout returns run.failed."""
        route = respx_mock.post("/chat-messages")
        route.side_effect = httpx.TimeoutException("Timeout")

        ctx = create_test_context(
            text="Hi",
            config={
                "base-url": "https://api.dify.ai/v1",
                "app-type": "chat",
                "api-key": "test-key",
                "timeout": 1,
            }
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        assert len(results) == 1
        assert results[0].type.value == "run.failed"
        assert results[0].data["code"] == "dify.timeout"

    @pytest.mark.anyio
    @respx.mock(base_url="https://api.dify.ai/v1")
    async def test_error_event_failure(self, runner, respx_mock):
        """Test Dify error event returns run.failed."""
        events = [
            {"event": "error", "message": "Something went wrong"},
        ]
        sse_lines = "".join([f"data: {json.dumps(e)}\n\n" for e in events])

        respx_mock.post("/chat-messages").mock(
            return_value=httpx.Response(200, text=sse_lines)
        )

        ctx = create_test_context(
            text="Hi",
            config={
                "base-url": "https://api.dify.ai/v1",
                "app-type": "chat",
                "api-key": "test-key",
            }
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        assert len(results) == 1
        assert results[0].type.value == "run.failed"
        assert results[0].data["code"] == "dify.api_error"

    @pytest.mark.anyio
    @respx.mock(base_url="https://api.dify.ai/v1")
    async def test_no_response_failure(self, runner, respx_mock):
        """Test empty Dify response returns run.failed."""
        # No meaningful events
        events = []
        sse_lines = "".join([f"data: {json.dumps(e)}\n\n" for e in events])

        respx_mock.post("/chat-messages").mock(
            return_value=httpx.Response(200, text=sse_lines)
        )

        ctx = create_test_context(
            text="Hi",
            config={
                "base-url": "https://api.dify.ai/v1",
                "app-type": "chat",
                "api-key": "test-key",
            }
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        assert len(results) == 1
        assert results[0].type.value == "run.failed"
        assert results[0].data["code"] == "dify.api_error"

    @pytest.mark.anyio
    @respx.mock(base_url="https://api.dify.ai/v1")
    async def test_conversation_id_state_update(self, runner, respx_mock):
        """Test conversation_id is stored in scoped state."""
        events = [
            {"event": "message", "answer": "Hello"},
            {"event": "message_end", "conversation_id": "conv_789"},
        ]
        sse_lines = "".join([f"data: {json.dumps(e)}\n\n" for e in events])

        respx_mock.post("/chat-messages").mock(
            return_value=httpx.Response(200, text=sse_lines)
        )

        ctx = create_test_context(
            text="Hi",
            config={
                "base-url": "https://api.dify.ai/v1",
                "app-type": "chat",
                "api-key": "test-key",
            }
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        # Check state.updated event with proper scope
        state_update_found = False
        for r in results:
            if r.type.value == "state.updated":
                state_update_found = True
                assert r.data["scope"] == "conversation"
                assert r.data["key"] == "external.conversation_id"
                assert r.data["value"] == "conv_789"
                break

        assert state_update_found

    @pytest.mark.anyio
    @respx.mock(base_url="https://api.dify.ai/v1")
    async def test_state_conversation_id_used_as_dify_conversation_id(self, runner, respx_mock):
        """Test ctx.state.conversation['external.conversation_id'] is passed to Dify."""
        events = [
            {"event": "message", "answer": "Hello"},
            {"event": "message_end", "conversation_id": "conv_existing"},
        ]
        sse_lines = "".join([f"data: {json.dumps(e)}\n\n" for e in events])

        # We need to check that the conversation_id is passed in the request
        # Use a custom side effect to verify the request payload
        request_payload = None

        def capture_request(request):
            nonlocal request_payload
            request_payload = json.loads(request.content)
            return httpx.Response(200, text=sse_lines)

        respx_mock.post("/chat-messages").mock(side_effect=capture_request)

        ctx = create_test_context(
            text="Hi",
            config={
                "base-url": "https://api.dify.ai/v1",
                "app-type": "chat",
                "api-key": "test-key",
            },
            state=AgentRunState(
                conversation={"external.conversation_id": "conv_from_state"}
            ),
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        # Verify conversation_id from state was passed to Dify
        assert request_payload is not None
        assert request_payload.get("conversation_id") == "conv_from_state"

        # Verify run completed
        assert results[-1].type.value == "run.completed"

    @pytest.mark.anyio
    @respx.mock(base_url="https://api.dify.ai/v1")
    async def test_params_passed_to_dify_inputs(self, runner, respx_mock):
        """Test ctx.params are passed to Dify inputs."""
        events = [
            {"event": "message", "answer": "Hello"},
            {"event": "message_end", "conversation_id": "conv_123"},
        ]
        sse_lines = "".join([f"data: {json.dumps(e)}\n\n" for e in events])

        request_payload = None

        def capture_request(request):
            nonlocal request_payload
            request_payload = json.loads(request.content)
            return httpx.Response(200, text=sse_lines)

        respx_mock.post("/chat-messages").mock(side_effect=capture_request)

        ctx = create_test_context(
            text="Hi",
            config={
                "base-url": "https://api.dify.ai/v1",
                "app-type": "chat",
                "api-key": "test-key",
            },
            params={
                "custom_var": "value1",
                "workflow_input": "test_data",
            },
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        # Verify params were passed to Dify inputs
        assert request_payload is not None
        inputs = request_payload.get("inputs", {})
        assert inputs.get("custom_var") == "value1"
        assert inputs.get("workflow_input") == "test_data"

        # Verify run completed
        assert results[-1].type.value == "run.completed"

    @pytest.mark.anyio
    @respx.mock(base_url="https://api.dify.ai/v1")
    async def test_not_reading_conversation_id_from_config(self, runner, respx_mock):
        """Test that conversation_id is NOT read from ctx.config."""
        events = [
            {"event": "message", "answer": "Hello"},
            {"event": "message_end", "conversation_id": "conv_new"},
        ]
        sse_lines = "".join([f"data: {json.dumps(e)}\n\n" for e in events])

        request_payload = None

        def capture_request(request):
            nonlocal request_payload
            request_payload = json.loads(request.content)
            return httpx.Response(200, text=sse_lines)

        respx_mock.post("/chat-messages").mock(side_effect=capture_request)

        # Config has conversation_id but runner should NOT use it
        ctx = create_test_context(
            text="Hi",
            config={
                "base-url": "https://api.dify.ai/v1",
                "app-type": "chat",
                "api-key": "test-key",
                "conversation_id": "should_not_be_used",  # This should be ignored
            },
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        # Verify conversation_id from config was NOT passed to Dify (should be empty)
        assert request_payload is not None
        assert request_payload.get("conversation_id") == ""  # Empty, not from config

        # Verify state.updated with new conversation_id from Dify response
        state_update_found = False
        for r in results:
            if r.type.value == "state.updated":
                state_update_found = True
                assert r.data["value"] == "conv_new"
                break

        assert state_update_found

    @pytest.mark.anyio
    @respx.mock(base_url="https://api.dify.ai/v1")
    async def test_workflow_params_and_legacy_inputs(self, runner, respx_mock):
        """Test workflow mode uses params and derives legacy inputs."""
        events = [
            {"event": "workflow_started"},
            {"event": "node_started", "data": {"node_id": "n1", "node_type": "start", "title": "Start"}},
            {"event": "workflow_finished", "data": {"outputs": {"summary": "Done"}, "error": None}},
        ]
        sse_lines = "".join([f"data: {json.dumps(e)}\n\n" for e in events])

        request_payload = None

        def capture_request(request):
            nonlocal request_payload
            request_payload = json.loads(request.content)
            return httpx.Response(200, text=sse_lines)

        respx_mock.post("/workflows/run").mock(side_effect=capture_request)

        ctx = create_test_context(
            text="Run workflow with custom input",
            config={
                "base-url": "https://api.dify.ai/v1",
                "app-type": "workflow",
                "api-key": "test-key",
            },
            params={
                "custom_workflow_var": "custom_value",
            },
            conversation=ConversationContext(
                session_id="session_abc",
                conversation_id="conv_xyz",
            ),
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        # Verify inputs
        assert request_payload is not None
        inputs = request_payload.get("inputs", {})

        # Legacy inputs derived from context
        assert inputs.get("langbot_user_message_text") == "Run workflow with custom input"
        assert inputs.get("langbot_session_id") == "session_abc"
        assert inputs.get("langbot_conversation_id") == "conv_xyz"

        # User params included
        assert inputs.get("custom_workflow_var") == "custom_value"

        # Verify run completed
        assert results[-1].type.value == "run.completed"

    @pytest.mark.anyio
    @respx.mock(base_url="https://api.dify.ai/v1")
    async def test_base_prompt_fallback(self, runner, respx_mock):
        """Test base-prompt is used when input is empty."""
        events = [
            {"event": "message", "answer": "Response"},
            {"event": "message_end", "conversation_id": "conv_001"},
        ]
        sse_lines = "".join([f"data: {json.dumps(e)}\n\n" for e in events])

        respx_mock.post("/chat-messages").mock(
            return_value=httpx.Response(200, text=sse_lines)
        )

        ctx = create_test_context(
            text="",  # Empty input
            config={
                "base-url": "https://api.dify.ai/v1",
                "app-type": "chat",
                "api-key": "test-key",
                "base-prompt": "Default prompt text",
            }
        )

        results = []
        async for result in runner.run(ctx):
            results.append(result)

        assert len(results) >= 2
        assert results[-1].type.value == "run.completed"


class TestDifyClientHelpers:
    """Tests for helper functions."""

    def test_extract_text_from_output_string(self):
        """Test extract text from string."""
        from langbot_agent_runner_utils.dify_client import extract_text_from_output

        assert extract_text_from_output("Hello") == "Hello"
        assert extract_text_from_output("") == ""
        assert extract_text_from_output(None) == ""

    def test_extract_text_from_output_dict(self):
        """Test extract text from dict."""
        from langbot_agent_runner_utils.dify_client import extract_text_from_output

        assert extract_text_from_output({"content": "Hello"}) == "Hello"
        assert extract_text_from_output({"other": "data"}) == '{"other": "data"}'

    def test_extract_text_from_output_json_string(self):
        """Test extract text from JSON string."""
        from langbot_agent_runner_utils.dify_client import extract_text_from_output

        assert extract_text_from_output('{"content": "Hello"}') == "Hello"
        assert extract_text_from_output('{"other": "data"}') == '{"other": "data"}'

    def test_process_thinking_content(self):
        """Test thinking content processing."""
        from langbot_agent_runner_utils.dify_client import process_thinking_content

        content = "Hello"
        result, thinking = process_thinking_content(content)
        assert result == "Hello"
        assert thinking == ""

        # Test with proper thinking tags (<think>...</think>)
        content_with_think = "<think>Let me think...</think>Hello"
        result, thinking = process_thinking_content(content_with_think)
        assert "Let me think..." in thinking
        assert "Hello" in result

        # With remove_think=True
        result, thinking = process_thinking_content(content_with_think, remove_think=True)
        assert "Let me think..." not in result
        assert thinking == ""
