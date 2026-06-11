"""Stable HTTP MCP gateway backed by LangBot run-scoped Host APIs."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import threading
import typing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from langbot_plugin.entities.io.actions.enums import PluginToRuntimeAction

logger = logging.getLogger(__name__)

SERVER_INFO = {"name": "langbot-agent-platform-gateway", "version": "0.1.0"}


def jsonable(value: typing.Any) -> typing.Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def mcp_result(value: typing.Any) -> dict[str, typing.Any]:
    structured = jsonable(value)
    if not isinstance(structured, dict):
        structured = {"result": structured}
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(structured, ensure_ascii=False),
            }
        ],
        "structuredContent": structured,
    }


def jsonrpc_result(message_id: typing.Any, result: dict[str, typing.Any]) -> dict[str, typing.Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def jsonrpc_error(message_id: typing.Any, code: int, message: str) -> dict[str, typing.Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


class LangBotMCPGateway:
    """Long-lived MCP server for Agent Platform with run_id-filled calls."""

    def __init__(
        self,
        plugin: typing.Any,
        *,
        host: str,
        port: int,
        token: str,
        request_timeout: float = 60.0,
    ) -> None:
        self.plugin = plugin
        self.host = host
        self.port = port
        self.token = token
        self.request_timeout = request_timeout
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def endpoint(self) -> str:
        if self._server is None:
            raise RuntimeError("LangBot MCP gateway is not started")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/mcp"

    def start(self) -> None:
        if self._server is not None:
            return

        self._loop = asyncio.get_running_loop()
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: typing.Any) -> None:
                return

            def do_GET(self) -> None:
                if self.path != "/healthz":
                    self.send_error(404)
                    return
                self._write_json(200, {"ok": True})

            def do_POST(self) -> None:
                if self.path != "/mcp":
                    self.send_error(404)
                    return
                if not self._authorized():
                    self._write_json(401, jsonrpc_error(None, -32001, "unauthorized"))
                    return

                payload = self._read_json_payload()
                if isinstance(payload, Exception):
                    self._write_json(400, jsonrpc_error(None, -32700, f"Parse error: {payload}"))
                    return

                assert gateway._loop is not None
                future = asyncio.run_coroutine_threadsafe(
                    gateway.handle_request(payload),
                    gateway._loop,
                )
                try:
                    result = future.result(timeout=gateway.request_timeout)
                except Exception as e:
                    self._write_json(500, jsonrpc_error(None, -32000, str(e)))
                    return
                if result is None:
                    self._write_empty(202)
                    return
                self._write_json(200, result)

            def _authorized(self) -> bool:
                authorization = self.headers.get("Authorization", "")
                token = self.headers.get("X-LangBot-MCP-Gateway-Token", "")
                return hmac.compare_digest(
                    authorization,
                    f"Bearer {gateway.token}",
                ) or hmac.compare_digest(token, gateway.token)

            def _read_json_payload(self) -> typing.Any:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                try:
                    body = self.rfile.read(length).decode("utf-8")
                    return json.loads(body) if body else {}
                except Exception as e:
                    return e

            def _write_json(self, status: int, payload: typing.Any) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _write_empty(self, status: int) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="langbot-litellm-agent-platform-mcp-gateway",
            daemon=True,
        )
        self._thread.start()
        logger.info("LangBot MCP gateway started at %s", self.endpoint)

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2)

    async def handle_request(self, payload: typing.Any) -> typing.Any:
        if isinstance(payload, list):
            if not payload:
                return jsonrpc_error(None, -32600, "Invalid request")
            responses = []
            for item in payload:
                response = await self._handle_message(item)
                if response is not None:
                    responses.append(response)
            return responses or None
        return await self._handle_message(payload)

    async def _handle_message(self, message: typing.Any) -> dict[str, typing.Any] | None:
        if not isinstance(message, dict):
            return jsonrpc_error(None, -32600, "Invalid request")

        message_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        if message_id is None:
            return None

        if method == "initialize":
            return jsonrpc_result(
                message_id,
                {
                    "protocolVersion": str(params.get("protocolVersion") or "2025-06-18"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                },
            )
        if method == "ping":
            return jsonrpc_result(message_id, {})
        if method == "tools/list":
            return jsonrpc_result(message_id, {"tools": await self._mcp_tools()})
        if method == "tools/call":
            try:
                result = await self._call_tool(params)
            except Exception as e:
                return jsonrpc_error(message_id, -32000, str(e))
            return jsonrpc_result(message_id, result)
        return jsonrpc_error(message_id, -32601, f"Method not found: {method}")

    async def _mcp_tools(self) -> list[dict[str, typing.Any]]:
        return [
            {
                "name": "langbot_history_page",
                "description": "Page through authorized LangBot conversation history for the run_id from the current LangBot run instructions.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string"},
                        "conversation_id": {"type": "string"},
                        "before_cursor": {"type": "string"},
                        "after_cursor": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                        "direction": {"type": "string", "enum": ["backward", "forward"], "default": "backward"},
                        "include_artifacts": {"type": "boolean", "default": False},
                    },
                    "required": ["run_id"],
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "langbot_retrieve_knowledge",
                "description": "Retrieve documents from a LangBot knowledge base authorized for the supplied run_id.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string"},
                        "kb_id": {"type": "string"},
                        "query_text": {"type": "string"},
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                        "filters": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["run_id", "kb_id"],
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "langbot_call_tool",
                "description": "Call a LangBot tool authorized for the supplied run_id.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string"},
                        "tool_name": {"type": "string"},
                        "parameters": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["run_id", "tool_name"],
                    "additionalProperties": False,
                },
            },
        ]

    async def _call_tool(self, params: dict[str, typing.Any]) -> dict[str, typing.Any]:
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}

        if name == "langbot_history_page":
            run_id = self._require_run_id(arguments)
            result = await self._runtime_call(
                PluginToRuntimeAction.HISTORY_PAGE,
                {
                    "run_id": run_id,
                    "conversation_id": arguments.get("conversation_id"),
                    "before_cursor": arguments.get("before_cursor"),
                    "after_cursor": arguments.get("after_cursor"),
                    "limit": self._int_arg(arguments.get("limit"), default=50),
                    "direction": str(arguments.get("direction") or "backward"),
                    "include_artifacts": bool(arguments.get("include_artifacts", False)),
                },
                timeout=30,
            )
            return mcp_result(result)

        if name == "langbot_retrieve_knowledge":
            run_id = self._require_run_id(arguments)
            kb_id = str(arguments.get("kb_id") or "").strip()
            query_text = str(arguments.get("query_text") or arguments.get("query") or "").strip()
            if not kb_id:
                raise ValueError("kb_id is required")
            if not query_text:
                raise ValueError("query_text is required")
            top_k = self._int_arg(arguments.get("top_k"), default=5)
            filters = arguments.get("filters") or {}
            if not isinstance(filters, dict):
                filters = {}
            result = await self._runtime_call(
                PluginToRuntimeAction.RETRIEVE_KNOWLEDGE_BASE,
                {
                    "run_id": run_id,
                    "kb_id": kb_id,
                    "query_text": query_text,
                    "top_k": top_k,
                    "filters": filters,
                },
                timeout=30,
            )
            return mcp_result({"result": result.get("results", result)})

        if name == "langbot_call_tool":
            run_id = self._require_run_id(arguments)
            tool_name = str(arguments.get("tool_name") or "").strip()
            if not tool_name:
                raise ValueError("tool_name is required")
            parameters = arguments.get("parameters") or {}
            if not isinstance(parameters, dict):
                parameters = {}
            result = await self._runtime_call(
                PluginToRuntimeAction.CALL_TOOL,
                {
                    "run_id": run_id,
                    "tool_name": tool_name,
                    "parameters": parameters,
                },
                timeout=180,
            )
            return mcp_result(result.get("result", result))

        raise ValueError(f"Unknown LangBot gateway tool: {name}")

    async def _runtime_call(
        self,
        action: PluginToRuntimeAction,
        payload: dict[str, typing.Any],
        *,
        timeout: float,
    ) -> dict[str, typing.Any]:
        runtime_handler = getattr(self.plugin, "plugin_runtime_handler", None)
        if runtime_handler is None:
            raise RuntimeError("LangBot plugin runtime handler is not available")
        result = await runtime_handler.call_action(action, payload, timeout=timeout)
        if not isinstance(result, dict):
            return {"result": result}
        return result

    def _require_run_id(self, arguments: dict[str, typing.Any]) -> str:
        run_id = str(arguments.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        return run_id

    def _int_arg(self, value: typing.Any, *, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
