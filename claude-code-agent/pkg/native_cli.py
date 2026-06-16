"""Native CLI runner helpers for Claude Code."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
import typing
import uuid

from langbot_plugin.api.agent_tools import (
    AgentMCPServerConfig,
    AgentRunExternalTools,
    AgentRunMCPAccess,
    AgentRuntimeDaemonClient,
    AgentRuntimeDaemonError,
    agent_runtime_daemon_config_from_plugin_config,
    get_agent_runtime_daemon_hub,
)
from langbot_plugin.api.definition.components.agent_runner.runner import AgentRunner
from langbot_plugin.api.entities.builtin.agent_runner import AgentRunContext, AgentRunResult

SESSION_STATE_KEY = "external.claude_code_session_id"
SUPPORTED_LOCATIONS = {"local", "remote-ssh", "daemon"}


class NativeCliError(Exception):
    def __init__(self, message: str, *, code: str = "claude_code.error", retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


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


def _to_float(value: typing.Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: typing.Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_args(value: typing.Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    text = str(value).strip()
    return shlex.split(text) if text else []


def _parse_json_object(value: typing.Any, *, label: str) -> dict[str, typing.Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise NativeCliError(f"{label} must be a JSON object", code="claude_code.config_invalid") from exc
    if not isinstance(parsed, dict):
        raise NativeCliError(f"{label} must be a JSON object", code="claude_code.config_invalid")
    return parsed


def _parse_json_list(value: typing.Any, *, label: str) -> list[typing.Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return list(value)
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise NativeCliError(f"{label} must be a JSON array", code="claude_code.config_invalid") from exc
    if not isinstance(parsed, list):
        raise NativeCliError(f"{label} must be a JSON array", code="claude_code.config_invalid")
    return parsed


def _parse_config_args(value: typing.Any) -> list[str]:
    if isinstance(value, str) and value.strip().startswith("["):
        return [str(item) for item in _parse_json_list(value, label="args-json")]
    if isinstance(value, list):
        return [str(item) for item in value]
    return _parse_args(value)


def _mcp_server_to_config(server: AgentMCPServerConfig) -> dict[str, typing.Any]:
    if server.transport == "http":
        return {
            "type": "http",
            "url": server.url,
            "headers": dict(server.headers),
        }
    return {
        "command": server.command,
        "args": list(server.args),
        "env": dict(server.env),
    }


def _mcp_config_json(servers: list[AgentMCPServerConfig], extra_servers: list[typing.Any]) -> str:
    mcp_servers: dict[str, typing.Any] = {}
    for server in servers:
        mcp_servers[server.name] = _mcp_server_to_config(server)
    for item in extra_servers:
        if isinstance(item, dict) and item.get("name"):
            server_name = str(item["name"])
            server_config = dict(item)
            server_config.pop("name", None)
            mcp_servers[server_name] = server_config
    return json.dumps({"mcpServers": mcp_servers}, ensure_ascii=False, separators=(",", ":"))


def _event_text(event: dict[str, typing.Any]) -> str:
    event_type = str(event.get("type") or event.get("event") or "")
    if event_type in {"session.started", "turn.started", "turn.completed", "mcp.server.started"}:
        return ""
    if isinstance(event.get("text"), str):
        return str(event["text"])
    if isinstance(event.get("content"), str):
        return str(event["content"])
    message = event.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
    if isinstance(event.get("result"), str):
        return str(event["result"])
    return ""


def _event_session_id(event: dict[str, typing.Any]) -> str:
    value = event.get("session_id") or event.get("sessionId")
    if value:
        return str(value)
    session = event.get("session")
    if isinstance(session, dict) and (session.get("id") or session.get("session_id")):
        return str(session.get("id") or session.get("session_id"))
    return ""


def _remote_shell_command(workspace: str, argv: list[str], env: dict[str, str]) -> str:
    exports = [f"export {shlex.quote(key)}={shlex.quote(value)}" for key, value in env.items()]
    parts = [*exports]
    if workspace:
        quoted_workspace = shlex.quote(workspace)
        parts.append(f"mkdir -p {quoted_workspace}")
        parts.append(f"cd {quoted_workspace}")
    parts.append(f"exec {shlex.join(argv)}")
    return f"bash -lc {shlex.quote(' && '.join(parts))}"


def _input_text(ctx: AgentRunContext) -> str:
    return ctx.input.to_text().strip()


class NativeClaudeCodeRunner(AgentRunner):
    def _validate_config(self, ctx: AgentRunContext) -> dict[str, typing.Any]:
        data = ctx.config or {}
        location = str(data.get("location", "local") or "local").strip()
        if location not in SUPPORTED_LOCATIONS:
            raise NativeCliError("location must be local, remote-ssh, or daemon", code="claude_code.config_invalid")
        workspace = str(data.get("workspace") or "").strip() or os.getcwd()
        ssh_target = str(data.get("ssh-target") or data.get("ssh_target") or "").strip()
        if location == "remote-ssh" and not ssh_target:
            raise NativeCliError("ssh-target is required when location=remote-ssh", code="claude_code.config_invalid")
        daemon_id = str(data.get("daemon-id") or data.get("daemon_id") or "").strip()
        if location == "daemon" and not daemon_id:
            raise NativeCliError("daemon-id is required when location=daemon", code="claude_code.config_invalid")
        return {
            "location": location,
            "workspace": workspace,
            "command": str(data.get("command") or "claude"),
            "args": _parse_config_args(data.get("args-json") or data.get("args")),
            "env": {str(k): str(v) for k, v in _parse_json_object(data.get("env-json"), label="env-json").items()},
            "ssh_target": ssh_target,
            "ssh_port": _to_int(data.get("ssh-port"), 22),
            "daemon_id": daemon_id,
            "daemon_connect_timeout": _to_float(data.get("daemon-connect-timeout"), 30.0),
            "timeout": _to_float(data.get("timeout"), 300.0),
            "streaming": _to_bool(data.get("streaming"), True),
            "reuse_session": _to_bool(data.get("reuse-session"), True),
            "langbot_assets_enabled": _to_bool(data.get("langbot-assets-enabled"), True),
            "mcp_bridge_transport": str(data.get("mcp-bridge-transport", "auto") or "auto").strip(),
            "mcp_servers": _parse_json_list(data.get("mcp-servers-json"), label="mcp-servers-json"),
            "daemon_hub": agent_runtime_daemon_config_from_plugin_config(
                self.get_plugin_config(),
                env_prefix="LANGBOT_CLAUDE_CODE_DAEMON",
                default_port=8767,
            ),
        }

    def _stored_session_id(self, ctx: AgentRunContext) -> str:
        return str(ctx.state.conversation.get(SESSION_STATE_KEY) or "").strip()

    def _session_id(self, ctx: AgentRunContext, config: dict[str, typing.Any]) -> tuple[str, bool]:
        stored = self._stored_session_id(ctx)
        if stored and config["reuse_session"]:
            return stored, False
        return str(uuid.uuid4()), True

    def _argv(self, config: dict[str, typing.Any], *, prompt: str, session_id: str, mcp_config: str) -> list[str]:
        argv = [*_parse_args(config["command"]), *config["args"], "-p", "--verbose", "--output-format", "stream-json"]
        if mcp_config:
            argv.extend(["--strict-mcp-config", "--mcp-config", mcp_config])
        if session_id:
            argv.extend(["--session-id", session_id])
        argv.append(prompt)
        return argv

    def _mcp_access(self, ctx: AgentRunContext, config: dict[str, typing.Any]) -> AgentRunMCPAccess | None:
        if not config["langbot_assets_enabled"]:
            return None
        access = AgentRunMCPAccess(
            self.get_run_api(ctx),
            ctx,
            enabled=True,
            location=config["location"],
            mode="ephemeral",
            transport=config["mcp_bridge_transport"],
            bridge_request_timeout=config["timeout"],
        )
        access.start()
        return access

    async def run(self, ctx: AgentRunContext) -> typing.AsyncGenerator[AgentRunResult, None]:
        try:
            config = self._validate_config(ctx)
            prompt = _input_text(ctx)
            if not prompt:
                raise NativeCliError("input text is required", code="claude_code.empty_input")
            if config["location"] == "daemon":
                async for result in self._run_daemon(ctx, config, prompt):
                    yield result
                return
            session_id, session_created = self._session_id(ctx, config)
            if session_created:
                yield AgentRunResult.state_updated(ctx.run_id, SESSION_STATE_KEY, session_id, scope="conversation")
            async for result in self._run_local_or_ssh(ctx, config, prompt, session_id):
                yield result
        except NativeCliError as exc:
            yield AgentRunResult.run_failed(ctx.run_id, error=exc.message, code=exc.code, retryable=exc.retryable)
        except AgentRuntimeDaemonError as exc:
            yield AgentRunResult.run_failed(ctx.run_id, error=exc.message, code=exc.code, retryable=exc.retryable)

    async def _run_local_or_ssh(
        self,
        ctx: AgentRunContext,
        config: dict[str, typing.Any],
        prompt: str,
        session_id: str,
    ) -> typing.AsyncGenerator[AgentRunResult, None]:
        access = self._mcp_access(ctx, config)
        try:
            mcp_servers = [access.server_config] if access and access.server_config else []
            mcp_config = _mcp_config_json(mcp_servers, config["mcp_servers"]) if mcp_servers or config["mcp_servers"] else ""
            argv = self._argv(config, prompt=prompt, session_id=session_id, mcp_config=mcp_config)
            env = {**os.environ, **config["env"]}
            command = argv[0]
            args = argv[1:]
            cwd = config["workspace"] if config["location"] == "local" else None
            if config["location"] == "remote-ssh":
                ssh_args = ["-T", "-p", str(config["ssh_port"])]
                if access and access.reverse_tunnel:
                    ssh_args.extend(access.reverse_tunnel.ssh_args())
                ssh_args.extend([config["ssh_target"], _remote_shell_command(config["workspace"], argv, config["env"])])
                command = "ssh"
                args = ssh_args
            async for result in _run_cli_process(
                ctx,
                command,
                args,
                cwd=cwd,
                env=env,
                timeout=config["timeout"],
                streaming=config["streaming"],
                expected_session_id=session_id,
            ):
                yield result
        finally:
            if access is not None:
                access.stop()

    async def _run_daemon(
        self,
        ctx: AgentRunContext,
        config: dict[str, typing.Any],
        prompt: str,
    ) -> typing.AsyncGenerator[AgentRunResult, None]:
        hub = get_agent_runtime_daemon_hub("claude-code", error_code_prefix="claude_code")
        if not hub.is_running:
            await hub.start(
                host=config["daemon_hub"]["host"],
                port=config["daemon_hub"]["port"],
                token=config["daemon_hub"]["token"],
            )
        session_id, session_created = self._session_id(ctx, config)
        if session_created:
            yield AgentRunResult.state_updated(ctx.run_id, SESSION_STATE_KEY, session_id, scope="conversation")
        tools = AgentRunExternalTools(self.get_run_api(ctx), ctx) if config["langbot_assets_enabled"] else None
        await hub.wait_for_daemon(config["daemon_id"], config["daemon_connect_timeout"])
        payload = {
            "prompt": prompt,
            "session_id": session_id,
            "config": {
                "command": config["command"],
                "args": config["args"],
                "workspace": config["workspace"],
                "env": config["env"],
                "timeout": config["timeout"],
                "streaming": config["streaming"],
                "mcp_servers": config["mcp_servers"],
                "langbot_assets_enabled": config["langbot_assets_enabled"],
            },
        }
        async for event in hub.run_job(
            daemon_id=config["daemon_id"],
            payload=payload,
            tools=tools,
            timeout=config["timeout"],
        ):
            event.setdefault("run_id", ctx.run_id)
            yield AgentRunResult.model_validate(event)


class NativeClaudeCodeDaemon(AgentRuntimeDaemonClient):
    async def run_job(self, job_id: str, payload: dict[str, typing.Any]) -> None:
        proxy = None
        config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
        try:
            mcp_servers: list[AgentMCPServerConfig] = []
            if config.get("langbot_assets_enabled", True):
                proxy = self.create_mcp_proxy(job_id, request_timeout=float(config.get("timeout") or 300.0))
                proxy.start()
                mcp_servers.append(proxy.mcp_server())
            mcp_config = _mcp_config_json(mcp_servers, list(config.get("mcp_servers") or [])) if mcp_servers else ""
            argv = [
                *_parse_args(config.get("command") or "claude"),
                *list(config.get("args") or []),
                "-p",
                "--verbose",
                "--output-format",
                "stream-json",
            ]
            if mcp_config:
                argv.extend(["--strict-mcp-config", "--mcp-config", mcp_config])
            session_id = str(payload.get("session_id") or "")
            if session_id:
                argv.extend(["--session-id", session_id])
            argv.append(str(payload.get("prompt") or ""))
            async for event in _run_cli_process_events(
                argv[0],
                argv[1:],
                cwd=str(config.get("workspace") or os.getcwd()),
                env={**os.environ, **{str(k): str(v) for k, v in dict(config.get("env") or {}).items()}},
                timeout=float(config.get("timeout") or 300.0),
                streaming=bool(config.get("streaming", True)),
                expected_session_id=session_id,
            ):
                await self.emit_event(job_id, event)
        finally:
            if proxy is not None:
                proxy.stop()


async def _run_cli_process(
    ctx: AgentRunContext,
    command: str,
    args: list[str],
    *,
    cwd: str | None,
    env: dict[str, str],
    timeout: float,
    streaming: bool,
    expected_session_id: str,
) -> typing.AsyncGenerator[AgentRunResult, None]:
    async for event in _run_cli_process_events(
        command,
        args,
        cwd=cwd,
        env=env,
        timeout=timeout,
        streaming=streaming,
        expected_session_id=expected_session_id,
    ):
        event.setdefault("run_id", ctx.run_id)
        yield AgentRunResult.model_validate(event)


async def _run_cli_process_events(
    command: str,
    args: list[str],
    *,
    cwd: str | None,
    env: dict[str, str],
    timeout: float,
    streaming: bool,
    expected_session_id: str,
) -> typing.AsyncGenerator[dict[str, typing.Any], None]:
    process = await asyncio.create_subprocess_exec(
        command,
        *args,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    deadline = time.monotonic() + timeout
    sequence = 0
    final_parts: list[str] = []
    stderr_task = asyncio.create_task(process.stderr.read())
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                raise NativeCliError("Claude Code run timed out", code="claude_code.timeout", retryable=True)
            line = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            parsed = _parse_cli_event(text)
            if parsed.get("type") == "error":
                raise NativeCliError(str(parsed.get("message") or parsed), code=str(parsed.get("code") or "claude_code.cli_error"))
            session_id = _event_session_id(parsed)
            if session_id and session_id != expected_session_id:
                yield {"type": "state.updated", "data": {"key": SESSION_STATE_KEY, "value": session_id, "scope": "conversation"}}
            chunk = _event_text(parsed)
            if chunk:
                final_parts.append(chunk)
                if streaming:
                    sequence += 1
                    yield {
                        "type": "message.delta",
                        "sequence": sequence,
                        "data": {
                            "chunk": {
                                "role": "assistant",
                                "content": chunk,
                                "all_content": "".join(final_parts),
                                "msg_sequence": sequence,
                            }
                        },
                    }
        returncode = await asyncio.wait_for(process.wait(), timeout=max(0.1, deadline - time.monotonic()))
        stderr = (await stderr_task).decode("utf-8", errors="replace").strip()
        if returncode != 0:
            raise NativeCliError(stderr or f"Claude Code exited with status {returncode}", code="claude_code.process_failed")
        final_text = "".join(final_parts).strip()
        if not final_text:
            raise NativeCliError("Claude Code returned no assistant text", code="claude_code.empty_response")
        yield {"type": "message.completed", "data": {"message": {"role": "assistant", "content": final_text}}}
        yield {"type": "run.completed", "data": {"finish_reason": "stop"}}
    finally:
        if not stderr_task.done():
            stderr_task.cancel()


def _parse_cli_event(line: str) -> dict[str, typing.Any]:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return {"type": "message.completed", "text": line}
    return parsed if isinstance(parsed, dict) else {"type": "message.completed", "text": line}
