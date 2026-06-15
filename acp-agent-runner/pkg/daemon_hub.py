"""WebSocket daemon hub for ACP runner remote clients.

The hub is intentionally plugin-local. LangBot still sees a normal
AgentRunner execution; this module only gives the ACP runner another transport
besides local subprocess and SSH subprocess.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import secrets
import time
import typing

import websockets
from langbot_plugin.api.agent_tools.external_tools import AgentRunExternalTools

logger = logging.getLogger(__name__)

DEFAULT_DAEMON_HUB_HOST = "127.0.0.1"
DEFAULT_DAEMON_HUB_PORT = 8766
DEFAULT_DAEMON_CONNECT_TIMEOUT = 30.0


class DaemonHubError(Exception):
    """Daemon hub error surfaced as an AgentRunner failure."""

    def __init__(self, message: str, *, code: str = "acp.daemon_error", retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


class DaemonConnection(typing.TypedDict):
    daemon_id: str
    websocket: typing.Any
    metadata: dict[str, typing.Any]
    connected_at: float
    last_seen_at: float
    active_jobs: set[str]


class DaemonRunSession(typing.TypedDict):
    job_id: str
    daemon_id: str
    queue: asyncio.Queue[dict[str, typing.Any] | None]
    tools: AgentRunExternalTools | None
    started_at: float


def _to_bool(value: typing.Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _to_int(value: typing.Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: typing.Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def daemon_hub_config_from_plugin_config(config: dict[str, typing.Any] | None) -> dict[str, typing.Any]:
    """Resolve daemon hub settings from plugin config and environment."""

    data = dict(config or {})
    return {
        "enabled": _to_bool(
            data.get("daemon-enabled", os.environ.get("LANGBOT_ACP_DAEMON_ENABLED")),
            False,
        ),
        "host": str(
            data.get("daemon-host")
            or os.environ.get("LANGBOT_ACP_DAEMON_HOST")
            or DEFAULT_DAEMON_HUB_HOST
        ),
        "port": _to_int(
            data.get("daemon-port") or os.environ.get("LANGBOT_ACP_DAEMON_PORT"),
            DEFAULT_DAEMON_HUB_PORT,
        ),
        "token": str(data.get("daemon-token") or os.environ.get("LANGBOT_ACP_DAEMON_TOKEN") or ""),
    }


def _jsonrpc_result(message_id: typing.Any, result: dict[str, typing.Any]) -> dict[str, typing.Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _jsonrpc_error(message_id: typing.Any, code: int, message: str) -> dict[str, typing.Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


async def _handle_mcp_message(
    tools: AgentRunExternalTools,
    message: typing.Any,
) -> dict[str, typing.Any] | None:
    if not isinstance(message, dict):
        return _jsonrpc_error(None, -32600, "Invalid request")

    message_id = message.get("id")
    method = str(message.get("method") or "")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    if message_id is None:
        return None

    if method == "initialize":
        return _jsonrpc_result(
            message_id,
            {
                "protocolVersion": str(params.get("protocolVersion") or "2025-06-18"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "langbot-agent-daemon", "version": "0.1.0"},
            },
        )
    if method == "ping":
        return _jsonrpc_result(message_id, {})
    if method == "tools/list":
        return _jsonrpc_result(message_id, {"tools": tools.mcp_tools()})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        return _jsonrpc_result(message_id, await tools.call_mcp_tool(name, arguments))
    return _jsonrpc_error(message_id, -32601, f"Method not found: {method}")


async def handle_mcp_payload(
    tools: AgentRunExternalTools,
    payload: typing.Any,
) -> dict[str, typing.Any] | list[dict[str, typing.Any]] | None:
    """Handle HTTP MCP JSON-RPC payloads forwarded by a daemon."""

    if isinstance(payload, list):
        if not payload:
            return _jsonrpc_error(None, -32600, "Invalid request")
        responses: list[dict[str, typing.Any]] = []
        for item in payload:
            response = await _handle_mcp_message(tools, item)
            if response is not None:
                responses.append(response)
        return responses or None
    return await _handle_mcp_message(tools, payload)


class DaemonHub:
    """Small WebSocket hub for user-owned runner daemons."""

    def __init__(self) -> None:
        self.host = DEFAULT_DAEMON_HUB_HOST
        self.port = DEFAULT_DAEMON_HUB_PORT
        self.token = ""
        self._server: typing.Any | None = None
        self._connections: dict[str, DaemonConnection] = {}
        self._jobs: dict[str, DaemonRunSession] = {}
        self._send_lock: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def endpoint(self) -> str:
        if self._server is None:
            return ""
        sockets = getattr(self._server, "sockets", None) or []
        if sockets:
            bound_host, bound_port = sockets[0].getsockname()[:2]
            return f"ws://{bound_host}:{bound_port}"
        return f"ws://{self.host}:{self.port}"

    async def start(self, *, host: str, port: int, token: str = "") -> None:
        if self._server is not None:
            if self.host == host and self.port == port and self.token == token:
                return
            raise DaemonHubError(
                f"ACP daemon hub already started at {self.endpoint}",
                code="acp.daemon_hub_conflict",
            )

        self.host = host
        self.port = port
        self.token = token
        self._server = await websockets.serve(self._handle_connection, host, port)
        logger.info("ACP daemon hub listening at %s", self.endpoint)

    async def ensure_started_from_config(self, config: dict[str, typing.Any]) -> None:
        hub_config = daemon_hub_config_from_plugin_config(config)
        await self.start(
            host=hub_config["host"],
            port=hub_config["port"],
            token=hub_config["token"],
        )

    async def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()

        connections = list(self._connections.values())
        self._connections.clear()
        self._send_lock.clear()
        for connection in connections:
            with contextlib.suppress(Exception):
                await connection["websocket"].close()

    async def list_daemons(self) -> list[dict[str, typing.Any]]:
        async with self._lock:
            return [
                {
                    "daemon_id": daemon_id,
                    "metadata": dict(connection["metadata"]),
                    "connected_at": connection["connected_at"],
                    "last_seen_at": connection["last_seen_at"],
                    "active_jobs": sorted(connection["active_jobs"]),
                }
                for daemon_id, connection in self._connections.items()
            ]

    async def wait_for_daemon(self, daemon_id: str, timeout: float) -> None:
        deadline = time.monotonic() + max(0.1, timeout)
        while True:
            async with self._lock:
                if daemon_id in self._connections:
                    return
            if time.monotonic() >= deadline:
                raise DaemonHubError(
                    f"ACP daemon {daemon_id} is not connected",
                    code="acp.daemon_offline",
                    retryable=True,
                )
            await asyncio.sleep(0.2)

    async def run_job(
        self,
        *,
        daemon_id: str,
        payload: dict[str, typing.Any],
        tools: AgentRunExternalTools | None,
        timeout: float,
    ) -> typing.AsyncGenerator[dict[str, typing.Any], None]:
        if self._server is None:
            raise DaemonHubError("ACP daemon hub is not started", code="acp.daemon_hub_not_started")

        connection = await self._get_connection(daemon_id)
        job_id = secrets.token_urlsafe(16)
        queue: asyncio.Queue[dict[str, typing.Any] | None] = asyncio.Queue()
        session: DaemonRunSession = {
            "job_id": job_id,
            "daemon_id": daemon_id,
            "queue": queue,
            "tools": tools,
            "started_at": time.monotonic(),
        }

        async with self._lock:
            self._jobs[job_id] = session
            connection["active_jobs"].add(job_id)

        try:
            await self._send(
                daemon_id,
                {
                    "type": "run.start",
                    "job_id": job_id,
                    "payload": payload,
                },
            )
            deadline = time.monotonic() + max(1.0, timeout)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DaemonHubError("ACP daemon run timed out", code="acp.daemon_timeout", retryable=True)
                item = await asyncio.wait_for(queue.get(), timeout=remaining)
                if item is None:
                    break
                yield item
        finally:
            async with self._lock:
                self._jobs.pop(job_id, None)
                current = self._connections.get(daemon_id)
                if current is not None:
                    current["active_jobs"].discard(job_id)
            with contextlib.suppress(Exception):
                await self._send(daemon_id, {"type": "run.cleanup", "job_id": job_id})

    async def _get_connection(self, daemon_id: str) -> DaemonConnection:
        async with self._lock:
            connection = self._connections.get(daemon_id)
        if connection is None:
            raise DaemonHubError(f"ACP daemon {daemon_id} is not connected", code="acp.daemon_offline", retryable=True)
        return connection

    async def _send(self, daemon_id: str, message: dict[str, typing.Any]) -> None:
        connection = await self._get_connection(daemon_id)
        lock = self._send_lock.setdefault(daemon_id, asyncio.Lock())
        async with lock:
            await connection["websocket"].send(json.dumps(message, ensure_ascii=False, separators=(",", ":")))

    async def _handle_connection(self, websocket: typing.Any) -> None:
        daemon_id = ""
        try:
            raw_hello = await asyncio.wait_for(websocket.recv(), timeout=10)
            hello = json.loads(raw_hello)
            if not isinstance(hello, dict) or hello.get("type") != "daemon.hello":
                await websocket.close(code=1008, reason="daemon.hello required")
                return

            daemon_id = str(hello.get("daemon_id") or "").strip()
            if not daemon_id:
                await websocket.close(code=1008, reason="daemon_id required")
                return

            provided_token = str(hello.get("token") or "")
            if self.token and not hmac.compare_digest(provided_token, self.token):
                await websocket.close(code=1008, reason="invalid token")
                return

            metadata = hello.get("metadata") if isinstance(hello.get("metadata"), dict) else {}
            now = time.monotonic()
            async with self._lock:
                old_connection = self._connections.pop(daemon_id, None)
                if old_connection is not None:
                    with contextlib.suppress(Exception):
                        await old_connection["websocket"].close(code=1012, reason="replaced")
                self._connections[daemon_id] = {
                    "daemon_id": daemon_id,
                    "websocket": websocket,
                    "metadata": dict(metadata),
                    "connected_at": now,
                    "last_seen_at": now,
                    "active_jobs": set(),
                }
                self._send_lock.setdefault(daemon_id, asyncio.Lock())

            await websocket.send(
                json.dumps(
                    {"type": "daemon.ready", "daemon_id": daemon_id, "server_time": time.time()},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            async for raw_message in websocket:
                await self._handle_message(daemon_id, raw_message)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:
            logger.exception("ACP daemon connection failed: daemon_id=%s", daemon_id or "<unregistered>")
        finally:
            if daemon_id:
                await self._drop_connection(daemon_id, websocket)

    async def _drop_connection(self, daemon_id: str, websocket: typing.Any) -> None:
        async with self._lock:
            connection = self._connections.get(daemon_id)
            if connection is not None and connection["websocket"] is websocket:
                self._connections.pop(daemon_id, None)
                self._send_lock.pop(daemon_id, None)
                for job_id in list(connection["active_jobs"]):
                    session = self._jobs.get(job_id)
                    if session is not None:
                        session["queue"].put_nowait(
                            {
                                "type": "run.failed",
                                "data": {
                                    "error": f"ACP daemon {daemon_id} disconnected",
                                    "code": "acp.daemon_disconnected",
                                    "retryable": True,
                                },
                            }
                        )
                        session["queue"].put_nowait(None)

    async def _handle_message(self, daemon_id: str, raw_message: str) -> None:
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.warning("Ignoring invalid ACP daemon JSON message from %s", daemon_id)
            return
        if not isinstance(message, dict):
            return

        connection = self._connections.get(daemon_id)
        if connection is not None:
            connection["last_seen_at"] = time.monotonic()

        message_type = str(message.get("type") or "")
        if message_type == "daemon.ping":
            await self._send(daemon_id, {"type": "daemon.pong", "time": time.time()})
            return
        if message_type == "run.event":
            await self._handle_run_event(message)
            return
        if message_type == "run.finished":
            await self._finish_job(str(message.get("job_id") or ""))
            return
        if message_type == "mcp.request":
            await self._handle_mcp_request(daemon_id, message)
            return
        logger.debug("Ignoring unknown ACP daemon message type from %s: %s", daemon_id, message_type)

    async def _handle_run_event(self, message: dict[str, typing.Any]) -> None:
        job_id = str(message.get("job_id") or "")
        session = self._jobs.get(job_id)
        if session is None:
            return
        event = message.get("event")
        if isinstance(event, dict):
            session["queue"].put_nowait(event)

    async def _finish_job(self, job_id: str) -> None:
        session = self._jobs.get(job_id)
        if session is not None:
            session["queue"].put_nowait(None)

    async def _handle_mcp_request(self, daemon_id: str, message: dict[str, typing.Any]) -> None:
        request_id = message.get("request_id")
        job_id = str(message.get("job_id") or "")
        session = self._jobs.get(job_id)
        tools = session.get("tools") if session is not None else None

        if tools is None:
            payload: typing.Any = _jsonrpc_error(None, -32000, "LangBot assets are unavailable for this run")
        else:
            try:
                payload = await handle_mcp_payload(tools, message.get("payload"))
            except Exception as exc:
                payload = _jsonrpc_error(None, -32000, str(exc))

        await self._send(
            daemon_id,
            {
                "type": "mcp.response",
                "request_id": request_id,
                "job_id": job_id,
                "payload": payload,
            },
        )


_global_hub: DaemonHub | None = None


def get_daemon_hub() -> DaemonHub:
    global _global_hub
    if _global_hub is None:
        _global_hub = DaemonHub()
    return _global_hub
