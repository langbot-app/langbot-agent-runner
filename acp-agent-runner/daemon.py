"""User-side daemon for ACP Agent Runner.

The daemon connects outward to the ACP runner plugin. It is useful when the
LangBot server cannot SSH into the user's workstation. The local ACP process
talks to a localhost MCP server; that server forwards LangBot asset requests
over the already-established WebSocket connection.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import secrets
import shlex
import threading
import time
import typing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import websockets
from pkg.acp_client import AcpError, AcpStdioClient

logger = logging.getLogger("langbot-acp-daemon")


def _parse_args(value: typing.Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    text = str(value).strip()
    if not text:
        return []
    return shlex.split(text)


def _extract_session_id(result: typing.Any) -> str:
    if not isinstance(result, dict):
        return ""
    session_id = result.get("sessionId") or result.get("session_id") or result.get("id")
    return str(session_id or "").strip()


def _runtime_has_method(capabilities: dict[str, typing.Any], method: str) -> bool:
    if method == "session/load":
        return bool(capabilities.get("loadSession"))
    if method.startswith("session/"):
        session_capabilities = capabilities.get("sessionCapabilities")
        if isinstance(session_capabilities, dict):
            capability_key = method.split("/", 1)[1].replace("-", "_")
            for key in (capability_key, method.split("/", 1)[1]):
                if key in session_capabilities:
                    value = session_capabilities[key]
                    return value is not None and value is not False
    methods = capabilities.get("methods")
    if isinstance(methods, list) and method in {str(item) for item in methods}:
        return True
    key = method.replace("/", "_").replace("-", "_")
    value = capabilities.get(method) or capabilities.get(key)
    return bool(value)


def _content_text(value: typing.Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    if value.get("type") == "text" and isinstance(value.get("text"), str):
        return str(value["text"])
    text = value.get("text")
    if isinstance(text, str):
        return text
    return ""


def _agent_text_from_update(update: dict[str, typing.Any]) -> str:
    payload = update.get("update") if isinstance(update.get("update"), dict) else update
    if not isinstance(payload, dict):
        return ""

    update_kind = str(payload.get("sessionUpdate") or payload.get("kind") or payload.get("type") or "")
    content = payload.get("content")

    if update_kind == "agent_message_chunk":
        return _content_text(content)

    if "agent_message" in update_kind or payload.get("role") == "assistant":
        if isinstance(content, list):
            return "".join(_content_text(item) for item in content)
        return _content_text(content)

    return ""


def _tool_update_payload(update: dict[str, typing.Any]) -> dict[str, typing.Any] | None:
    payload = update.get("update") if isinstance(update.get("update"), dict) else update
    if not isinstance(payload, dict):
        return None
    update_kind = str(payload.get("sessionUpdate") or payload.get("kind") or payload.get("type") or "")
    if "tool_call" not in update_kind:
        return None
    return payload


def _result_event(result_type: str, data: dict[str, typing.Any], *, sequence: int | None = None) -> dict[str, typing.Any]:
    event = {"type": result_type, "data": data}
    if sequence is not None:
        event["sequence"] = sequence
    return event


class LocalMCPProxy:
    """Localhost HTTP MCP proxy that forwards requests over the daemon WS."""

    def __init__(self, daemon: RunnerDaemon, job_id: str, *, request_timeout: float = 60.0) -> None:
        self.daemon = daemon
        self.job_id = job_id
        self.request_timeout = request_timeout
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def endpoint(self) -> str:
        if self._server is None:
            raise RuntimeError("MCP proxy is not started")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def http_mcp_endpoint(self) -> str:
        return f"{self.endpoint}/mcp"

    def start(self) -> None:
        if self._server is not None:
            return

        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: typing.Any) -> None:
                return

            def do_GET(self) -> None:
                if self.path != "/healthz":
                    self.send_error(404)
                    return
                self._write_json(200, {"ok": True})

            def do_POST(self) -> None:
                if self.path not in {"/mcp", "/mcp/http"}:
                    self.send_error(404)
                    return
                payload = self._read_json_payload()
                if isinstance(payload, Exception):
                    self._write_json(400, {"jsonrpc": "2.0", "id": None, "error": str(payload)})
                    return
                try:
                    result = proxy.daemon.request_mcp(proxy.job_id, payload, proxy.request_timeout)
                except Exception as exc:
                    result = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32000, "message": str(exc)},
                    }
                if result is None:
                    self._write_empty(202)
                    return
                self._write_json(200, result)

            def _read_json_payload(self) -> typing.Any:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                try:
                    body = self.rfile.read(length).decode("utf-8")
                    return json.loads(body) if body else {}
                except Exception as exc:
                    return exc

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

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="langbot-acp-mcp-proxy", daemon=True)
        self._thread.start()

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

    def server_config(self) -> dict[str, typing.Any]:
        return {
            "name": "langbot_agent",
            "type": "http",
            "url": self.http_mcp_endpoint,
            "headers": [],
        }


class RunnerDaemon:
    """Outbound WebSocket daemon that runs ACP jobs locally."""

    def __init__(
        self,
        *,
        url: str,
        daemon_id: str,
        token: str = "",
        reconnect_delay: float = 5.0,
    ) -> None:
        self.url = url
        self.daemon_id = daemon_id
        self.token = token
        self.reconnect_delay = reconnect_delay
        self.websocket: typing.Any | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._send_lock = asyncio.Lock()
        self._pending_mcp: dict[str, asyncio.Future[typing.Any]] = {}
        self._job_tasks: dict[str, asyncio.Task[None]] = {}

    async def run_forever(self) -> None:
        self.loop = asyncio.get_running_loop()
        while True:
            try:
                async with websockets.connect(self.url) as websocket:
                    self.websocket = websocket
                    await self._send(
                        {
                            "type": "daemon.hello",
                            "daemon_id": self.daemon_id,
                            "token": self.token,
                            "metadata": {
                                "pid": os.getpid(),
                                "cwd": os.getcwd(),
                                "platform": os.name,
                            },
                        }
                    )
                    raw_ready = await websocket.recv()
                    ready = json.loads(raw_ready)
                    if not isinstance(ready, dict) or ready.get("type") != "daemon.ready":
                        raise RuntimeError(f"Unexpected daemon ready response: {ready!r}")
                    logger.info("Connected to ACP daemon hub as %s", self.daemon_id)
                    async for raw_message in websocket:
                        await self._handle_message(raw_message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Disconnected from ACP daemon hub: %s", exc)
            finally:
                self.websocket = None
                for future in list(self._pending_mcp.values()):
                    if not future.done():
                        future.cancel()
                self._pending_mcp.clear()
            await asyncio.sleep(self.reconnect_delay)

    async def _send(self, message: dict[str, typing.Any]) -> None:
        if self.websocket is None:
            raise RuntimeError("daemon websocket is not connected")
        async with self._send_lock:
            await self.websocket.send(json.dumps(message, ensure_ascii=False, separators=(",", ":")))

    async def _handle_message(self, raw_message: str) -> None:
        message = json.loads(raw_message)
        if not isinstance(message, dict):
            return
        message_type = str(message.get("type") or "")
        if message_type == "daemon.pong":
            return
        if message_type == "run.start":
            job_id = str(message.get("job_id") or "")
            payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
            if not job_id:
                return
            task = asyncio.create_task(self._run_job(job_id, payload))
            self._job_tasks[job_id] = task
            task.add_done_callback(lambda _: self._job_tasks.pop(job_id, None))
            return
        if message_type == "run.cancel":
            job_id = str(message.get("job_id") or "")
            task = self._job_tasks.get(job_id)
            if task is not None:
                task.cancel()
            return
        if message_type == "run.cleanup":
            return
        if message_type == "mcp.response":
            request_id = str(message.get("request_id") or "")
            future = self._pending_mcp.pop(request_id, None)
            if future is not None and not future.done():
                future.set_result(message.get("payload"))
            return

    def request_mcp(self, job_id: str, payload: typing.Any, timeout: float) -> typing.Any:
        if self.loop is None:
            raise RuntimeError("daemon event loop is not running")
        request_id = secrets.token_urlsafe(12)
        future = asyncio.run_coroutine_threadsafe(
            self._request_mcp_async(job_id, request_id, payload),
            self.loop,
        )
        return future.result(timeout=timeout)

    async def _request_mcp_async(self, job_id: str, request_id: str, payload: typing.Any) -> typing.Any:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[typing.Any] = loop.create_future()
        self._pending_mcp[request_id] = future
        await self._send(
            {
                "type": "mcp.request",
                "request_id": request_id,
                "job_id": job_id,
                "payload": payload,
            }
        )
        return await future

    async def _emit(self, job_id: str, event: dict[str, typing.Any]) -> None:
        await self._send({"type": "run.event", "job_id": job_id, "event": event})

    async def _finish(self, job_id: str) -> None:
        await self._send({"type": "run.finished", "job_id": job_id})

    async def _run_job(self, job_id: str, payload: dict[str, typing.Any]) -> None:
        proxy: LocalMCPProxy | None = None
        client: AcpStdioClient | None = None
        try:
            config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
            prompt_text = str(payload.get("prompt_text") or "")
            if not prompt_text:
                raise AcpError("prompt_text is required", code="acp.daemon_empty_prompt")

            command_args = _parse_args(config.get("acp_command"))
            if not command_args:
                raise AcpError("acp_command is required", code="acp.daemon_config_invalid")

            mcp_servers = list(config.get("mcp_servers") or [])
            if config.get("langbot_assets_enabled", True):
                proxy = LocalMCPProxy(self, job_id, request_timeout=float(config.get("mcp_request_timeout") or 60.0))
                proxy.start()
                mcp_servers.append(proxy.server_config())

            client = AcpStdioClient(
                command=command_args[0],
                args=command_args[1:],
                cwd=str(config.get("cwd") or config.get("workspace") or os.getcwd()),
                env={str(k): str(v) for k, v in dict(config.get("env") or {}).items()},
                permission_decision=str(config.get("permission_decision") or "allow_once"),
                startup_timeout=float(config.get("startup_timeout") or 30.0),
            )

            async with client:
                initialize_result = await client.initialize(timeout=float(config.get("initialize_timeout") or 30.0))
                session_id, created = await self._create_or_resume_session(client, initialize_result, config, mcp_servers)
                stored_session_id = str(config.get("stored_session_id") or "")
                if created or stored_session_id != session_id:
                    await self._emit(
                        job_id,
                        _result_event(
                            "state.updated",
                            {
                                "key": "external.acp_session_id",
                                "value": session_id,
                                "scope": "conversation",
                            },
                        ),
                    )
                await self._stream_prompt_results(client, job_id, session_id, prompt_text, config)
        except asyncio.CancelledError:
            await self._emit(
                job_id,
                _result_event(
                    "run.failed",
                    {"error": "ACP daemon run cancelled", "code": "acp.daemon_cancelled", "retryable": True},
                ),
            )
            raise
        except TimeoutError:
            await self._emit(
                job_id,
                _result_event(
                    "run.failed",
                    {"error": "ACP daemon run timed out", "code": "acp.daemon_timeout", "retryable": True},
                ),
            )
        except AcpError as exc:
            await self._emit(
                job_id,
                _result_event("run.failed", {"error": exc.message, "code": exc.code, "retryable": exc.retryable}),
            )
        except Exception as exc:
            logger.exception("ACP daemon job failed: %s", exc)
            await self._emit(
                job_id,
                _result_event("run.failed", {"error": str(exc), "code": "acp.daemon_unexpected"}),
            )
        finally:
            if client and client.stderr_tail.strip():
                logger.debug("ACP stderr tail for %s: %s", job_id, client.stderr_tail.strip())
            if proxy is not None:
                proxy.stop()
            await self._finish(job_id)

    async def _create_or_resume_session(
        self,
        client: AcpStdioClient,
        initialize_result: dict[str, typing.Any],
        config: dict[str, typing.Any],
        mcp_servers: list[dict[str, typing.Any]],
    ) -> tuple[str, bool]:
        capabilities = initialize_result.get("agentCapabilities")
        if not isinstance(capabilities, dict):
            capabilities = {}

        stored_session_id = str(config.get("stored_session_id") or "").strip()
        timeout = float(config.get("timeout") or 300.0)
        cwd = str(config.get("session_cwd") or config.get("workspace") or os.getcwd())
        if stored_session_id and config.get("reuse_session", True):
            if _runtime_has_method(capabilities, "session/resume"):
                result = await client.request(
                    "session/resume",
                    {"sessionId": stored_session_id, "cwd": cwd, "mcpServers": mcp_servers},
                    timeout=timeout,
                )
                return _extract_session_id(result) or stored_session_id, False
            if _runtime_has_method(capabilities, "session/load"):
                result = await client.request(
                    "session/load",
                    {"sessionId": stored_session_id, "cwd": cwd, "mcpServers": mcp_servers},
                    timeout=timeout,
                )
                await client.drain_updates()
                return _extract_session_id(result) or stored_session_id, False

        if not config.get("create_session_if_missing", True):
            raise AcpError("no stored ACP session and create-session-if-missing is disabled", code="acp.session_missing")

        result = await client.request("session/new", {"mcpServers": mcp_servers, "cwd": cwd}, timeout=timeout)
        session_id = _extract_session_id(result)
        if not session_id:
            raise AcpError(f"ACP session/new did not return a session id: {result!r}", code="acp.response_invalid")
        return session_id, True

    async def _stream_prompt_results(
        self,
        client: AcpStdioClient,
        job_id: str,
        session_id: str,
        prompt_text: str,
        config: dict[str, typing.Any],
    ) -> None:
        prompt_request = client.send_request(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": prompt_text}]},
        )
        sequence = 0
        final_text_parts: list[str] = []
        active_tool_calls: set[str] = set()
        timeout = float(config.get("timeout") or 300.0)
        deadline = time.monotonic() + timeout
        streaming = bool(config.get("streaming", True))

        while True:
            if prompt_request.future.done():
                update = client.next_update_nowait()
                if update is None:
                    break
            else:
                if time.monotonic() >= deadline:
                    raise TimeoutError
                update = await client.next_update(timeout=0.1)
                if update is None:
                    continue

            text = _agent_text_from_update(update)
            if text:
                final_text_parts.append(text)
                if streaming:
                    sequence += 1
                    await self._emit(
                        job_id,
                        _result_event(
                            "message.delta",
                            {
                                "chunk": {
                                    "role": "assistant",
                                    "content": text,
                                    "all_content": "".join(final_text_parts),
                                    "msg_sequence": sequence,
                                }
                            },
                            sequence=sequence,
                        ),
                    )

            tool_payload = _tool_update_payload(update)
            if tool_payload:
                await self._emit_tool_update(job_id, tool_payload, active_tool_calls)

        await prompt_request.wait(timeout=timeout)
        final_text = "".join(final_text_parts).strip()
        if not final_text:
            await self._emit(
                job_id,
                _result_event("run.failed", {"error": "ACP agent returned no assistant text", "code": "acp.empty_response"}),
            )
            return

        await self._emit(
            job_id,
            _result_event("message.completed", {"message": {"role": "assistant", "content": final_text}}),
        )
        await self._emit(job_id, _result_event("run.completed", {"finish_reason": "stop"}))

    async def _emit_tool_update(
        self,
        job_id: str,
        tool_payload: dict[str, typing.Any],
        active_tool_calls: set[str],
    ) -> None:
        tool_call_id = str(tool_payload.get("toolCallId") or tool_payload.get("id") or "")
        tool_name = str(tool_payload.get("title") or tool_payload.get("name") or "acp_tool")
        status = str(tool_payload.get("status") or "")
        if tool_call_id and tool_call_id not in active_tool_calls:
            active_tool_calls.add(tool_call_id)
            await self._emit(
                job_id,
                _result_event(
                    "tool.call.started",
                    {"tool_call_id": tool_call_id, "tool_name": tool_name, "parameters": {}},
                ),
            )
        if tool_call_id and status in {"completed", "failed", "cancelled"}:
            await self._emit(
                job_id,
                _result_event(
                    "tool.call.completed",
                    {
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "result": tool_payload if status == "completed" else None,
                        "error": None if status == "completed" else json.dumps(tool_payload, ensure_ascii=False),
                    },
                ),
            )


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Connect a local ACP runtime to LangBot ACP Agent Runner.")
    parser.add_argument("--url", required=True, help="Daemon hub WebSocket URL, for example ws://host:8766")
    parser.add_argument("--daemon-id", required=True, help="Stable daemon id configured in the LangBot runner.")
    parser.add_argument("--token", default=os.environ.get("LANGBOT_ACP_DAEMON_TOKEN", ""), help="Shared hub token.")
    parser.add_argument("--reconnect-delay", type=float, default=5.0)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


async def async_main() -> None:
    args = parse_cli_args()
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO))
    daemon = RunnerDaemon(
        url=args.url,
        daemon_id=args.daemon_id,
        token=args.token,
        reconnect_delay=args.reconnect_delay,
    )
    await daemon.run_forever()


def main() -> int:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(async_main())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
