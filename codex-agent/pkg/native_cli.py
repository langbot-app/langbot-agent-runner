"""Native CLI runner helpers for Codex."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import typing
import uuid
from pathlib import Path

from langbot_plugin.api.agent_tools.daemon import (
    AgentRuntimeDaemonClient,
    AgentRuntimeDaemonError,
    agent_runtime_daemon_config_from_plugin_config,
    get_agent_runtime_daemon_hub,
)
from langbot_plugin.api.agent_tools.external_tools import AgentRunExternalTools
from langbot_plugin.api.agent_tools.mcp_access import AgentRunMCPAccess
from langbot_plugin.api.agent_tools.mcp_config import AgentMCPServerConfig
from langbot_plugin.api.definition.components.agent_runner.runner import AgentRunner
from langbot_plugin.api.entities.builtin.agent_runner import AgentRunContext, AgentRunResult

from pkg.steering import run_with_steering

SESSION_STATE_KEY = "external.codex_session_id"
SUPPORTED_LOCATIONS = {"local", "remote-ssh", "daemon"}
logger = logging.getLogger(__name__)


_AUTH_ASSIGNMENT_RE = re.compile(r"(?i)(\bAuthorization\b[\"']?\s*[:=]\s*[\"']?)(?:Bearer\s+)?[^\"'\s,}\]]+")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:run[_-]?token|mcp[_-]?token|langbot_agent_mcp_token|"
    r"langbot[_-]?asset[_-]?run[_-]?token|api[_-]?key|secret|password)\b"
    r"[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}\]]+"
)


def _redact_secrets(text: str) -> str:
    redacted = _AUTH_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", str(text))
    redacted = _BEARER_RE.sub("Bearer [REDACTED]", redacted)
    return _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", redacted)


class NativeCliError(Exception):
    def __init__(self, message: str, *, code: str = "codex.error", retryable: bool = False) -> None:
        redacted_message = _redact_secrets(message)
        super().__init__(redacted_message)
        self.message = redacted_message
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
        raise NativeCliError(f"{label} must be a JSON object", code="codex.config_invalid") from exc
    if not isinstance(parsed, dict):
        raise NativeCliError(f"{label} must be a JSON object", code="codex.config_invalid")
    return parsed


def _parse_json_list(value: typing.Any, *, label: str) -> list[typing.Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return list(value)
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise NativeCliError(f"{label} must be a JSON array", code="codex.config_invalid") from exc
    if not isinstance(parsed, list):
        raise NativeCliError(f"{label} must be a JSON array", code="codex.config_invalid")
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


def _mcp_servers_config(servers: list[AgentMCPServerConfig], extra_servers: list[typing.Any]) -> dict[str, typing.Any]:
    mcp_servers: dict[str, typing.Any] = {}
    for server in servers:
        mcp_servers[server.name] = _mcp_server_to_config(server)
    for item in extra_servers:
        if isinstance(item, dict) and item.get("name"):
            server_name = str(item["name"])
            server_config = dict(item)
            server_config.pop("name", None)
            mcp_servers[server_name] = server_config
    return mcp_servers


_BARE_TOML_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MANAGED_MCP_BEGIN = "# BEGIN langbot-managed mcp_servers (do not edit; regenerated by codex-agent)"
_MANAGED_MCP_END = "# END langbot-managed mcp_servers"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_key(value: str) -> str:
    return value if _BARE_TOML_KEY_RE.match(value) else _toml_string(value)


def _toml_value(value: typing.Any) -> str:
    if value is None:
        raise NativeCliError("mcp server config cannot contain null values", code="codex.config_invalid")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        parts = [f"{_toml_key(str(key))} = {_toml_value(value[key])}" for key in sorted(value)]
        return "{ " + ", ".join(parts) + " }"
    raise NativeCliError(f"unsupported mcp config value type: {type(value).__name__}", code="codex.config_invalid")


def _mcp_config_toml(mcp_servers: dict[str, typing.Any]) -> str:
    if not mcp_servers:
        return ""
    lines = [_MANAGED_MCP_BEGIN]
    for index, name in enumerate(sorted(mcp_servers)):
        if index:
            lines.append("")
        if not _BARE_TOML_KEY_RE.match(name):
            raise NativeCliError(
                f"mcp server name {name!r} must contain only ASCII letters, digits, '_' or '-'",
                code="codex.config_invalid",
            )
        server = mcp_servers[name]
        if not isinstance(server, dict):
            raise NativeCliError(f"mcp server {name!r} must be a JSON object", code="codex.config_invalid")
        lines.append(f"[mcp_servers.{name}]")
        for key in sorted(server):
            lines.append(f"{_toml_key(str(key))} = {_toml_value(server[key])}")
    lines.append(_MANAGED_MCP_END)
    return "\n".join(lines) + "\n"


def _safe_home_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return safe[:96] or f"run-{uuid.uuid4()}"


def _per_run_home_name(home_key: str) -> str:
    return f"{_safe_home_name(home_key)}-{uuid.uuid4().hex[:12]}"


def _shared_codex_home(env: dict[str, str]) -> Path:
    configured = env.get("CODEX_HOME") or os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


def _symlink_or_copy_file(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        dst.symlink_to(src)
    except OSError:
        shutil.copy2(src, dst)


def _symlink_or_copy_dir(src: Path, dst: Path) -> None:
    if not src.exists() or not src.is_dir():
        return
    if dst.exists() or dst.is_symlink():
        if dst.is_symlink():
            dst.unlink()
        return
    try:
        dst.symlink_to(src, target_is_directory=True)
    except OSError:
        shutil.copytree(src, dst, dirs_exist_ok=True)


def _prepare_local_codex_home(
    workspace: str,
    home_key: str,
    env: dict[str, str],
    mcp_toml: str,
) -> tuple[dict[str, str], str]:
    workspace_path = Path(workspace).expanduser().resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)
    home_parent = workspace_path / ".langbot-codex-home"
    home_parent.mkdir(parents=True, exist_ok=True)
    home = home_parent / _per_run_home_name(home_key)
    home.mkdir(mode=0o700)
    home.chmod(0o700)

    shared = _shared_codex_home(env)
    _symlink_or_copy_file(shared / "auth.json", home / "auth.json")
    shared_sessions = shared / "sessions"
    shared_sessions.mkdir(parents=True, exist_ok=True)
    _symlink_or_copy_dir(shared_sessions, home / "sessions")
    for name in ("config.json", "config.toml", "instructions.md"):
        src = shared / name
        if src.exists():
            shutil.copy2(src, home / name)
    config_path = home / "config.toml"
    if not config_path.exists():
        config_path.write_text("", encoding="utf-8")
    if mcp_toml:
        with config_path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
            handle.write(mcp_toml)
        config_path.chmod(0o600)

    updated_env = dict(env)
    updated_env["CODEX_HOME"] = str(home)
    return updated_env, str(workspace_path)


def _remote_mcp_stdin_prelude(mcp_toml: str) -> bytes:
    if not mcp_toml:
        return b""
    return base64.b64encode(("\n" + mcp_toml).encode("utf-8")) + b"\n"


def _remote_codex_home_lines(home_key: str, *, read_mcp_from_stdin: bool) -> list[str]:
    run_home = f".langbot-codex-home/{_per_run_home_name(home_key)}"
    lines = [
        f"run_home={shlex.quote(run_home)}",
        'shared_home="${CODEX_HOME:-$HOME/.codex}"',
        'mkdir -p "$run_home"',
        '[ -f "$shared_home/auth.json" ] && { ln -s "$shared_home/auth.json" "$run_home/auth.json" 2>/dev/null || cp "$shared_home/auth.json" "$run_home/auth.json"; } || :',
        'mkdir -p "$shared_home/sessions"',
        'ln -s "$shared_home/sessions" "$run_home/sessions" 2>/dev/null || cp -R "$shared_home/sessions" "$run_home/sessions"',
        '[ -f "$shared_home/config.json" ] && cp "$shared_home/config.json" "$run_home/config.json" || :',
        '[ -f "$shared_home/config.toml" ] && cp "$shared_home/config.toml" "$run_home/config.toml" || :',
        '[ -f "$shared_home/instructions.md" ] && cp "$shared_home/instructions.md" "$run_home/instructions.md" || :',
        'touch "$run_home/config.toml"',
    ]
    if read_mcp_from_stdin:
        lines.append('IFS= read -r langbot_mcp_config_b64 || langbot_mcp_config_b64=""')
        lines.append('[ -n "$langbot_mcp_config_b64" ] && printf %s "$langbot_mcp_config_b64" | base64 -d >> "$run_home/config.toml" || :')
        lines.append('chmod 600 "$run_home/config.toml"')
    lines.append('export CODEX_HOME="$PWD/$run_home"')
    return lines


def _remote_shell_command(
    workspace: str,
    argv: list[str],
    env: dict[str, str],
    *,
    home_key: str,
    read_mcp_from_stdin: bool = False,
) -> str:
    lines = ["set -e"]
    lines.extend(f"export {shlex.quote(key)}={shlex.quote(value)}" for key, value in env.items())
    if workspace:
        quoted_workspace = shlex.quote(workspace)
        lines.append(f"mkdir -p {quoted_workspace}")
        lines.append(f"cd {quoted_workspace}")
    lines.extend(_remote_codex_home_lines(home_key, read_mcp_from_stdin=read_mcp_from_stdin))
    lines.append(f"exec {shlex.join(argv)}")
    return f"bash -lc {shlex.quote(chr(10).join(lines))}"


def _input_text(ctx: AgentRunContext) -> str:
    return ctx.input.to_text().strip()


class NativeCodexRunner(AgentRunner):
    def _validate_config(self, ctx: AgentRunContext) -> dict[str, typing.Any]:
        data = ctx.config or {}
        location = str(data.get("location", "local") or "local").strip()
        if location not in SUPPORTED_LOCATIONS:
            raise NativeCliError("location must be local, remote-ssh, or daemon", code="codex.config_invalid")
        workspace = str(data.get("workspace") or "").strip() or os.getcwd()
        ssh_target = str(data.get("ssh-target") or data.get("ssh_target") or "").strip()
        if location == "remote-ssh" and not ssh_target:
            raise NativeCliError("ssh-target is required when location=remote-ssh", code="codex.config_invalid")
        daemon_id = str(data.get("daemon-id") or data.get("daemon_id") or "").strip()
        if location == "daemon" and not daemon_id:
            raise NativeCliError("daemon-id is required when location=daemon", code="codex.config_invalid")
        return {
            "location": location,
            "workspace": workspace,
            "command": str(data.get("command") or "codex"),
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
                env_prefix="LANGBOT_CODEX_DAEMON",
                default_port=8768,
            ),
        }

    def _stored_session_id(self, ctx: AgentRunContext) -> str:
        return str(ctx.state.conversation.get(SESSION_STATE_KEY) or "").strip()

    def _resume_session_id(self, ctx: AgentRunContext, config: dict[str, typing.Any]) -> str:
        stored = self._stored_session_id(ctx)
        if stored and config["reuse_session"]:
            return stored
        return ""

    def _argv(self, config: dict[str, typing.Any]) -> list[str]:
        return [*_parse_args(config["command"]), "app-server", "--listen", "stdio://", *config["args"]]

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
                raise NativeCliError("input text is required", code="codex.empty_input")

            def run_turn(
                turn_prompt: str, resume_session_id: str
            ) -> typing.AsyncGenerator[AgentRunResult, None]:
                if config["location"] == "daemon":
                    return self._run_daemon(ctx, config, turn_prompt, resume_session_id)
                return self._run_local_or_ssh(ctx, config, turn_prompt, resume_session_id)

            async for result in run_with_steering(
                ctx,
                lambda: self.get_run_api(ctx),
                run_turn,
                initial_prompt=prompt,
                initial_resume_session_id=self._resume_session_id(ctx, config),
                session_state_key=SESSION_STATE_KEY,
            ):
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
            mcp_servers = _mcp_servers_config([access.server_config] if access and access.server_config else [], config["mcp_servers"])
            mcp_toml = _mcp_config_toml(mcp_servers)
            argv = self._argv(config)
            env = {**os.environ, **config["env"]}
            command = argv[0]
            args = argv[1:]
            cwd = config["workspace"] if config["location"] == "local" else None
            initial_stdin = b""
            if config["location"] == "remote-ssh":
                ssh_args = ["-T", "-p", str(config["ssh_port"])]
                if access and access.reverse_tunnel:
                    ssh_args.extend(access.reverse_tunnel.ssh_args())
                initial_stdin = _remote_mcp_stdin_prelude(mcp_toml)
                ssh_args.extend(
                    [
                        config["ssh_target"],
                        _remote_shell_command(
                            config["workspace"],
                            argv,
                            config["env"],
                            home_key=session_id or ctx.run_id,
                            read_mcp_from_stdin=bool(mcp_toml),
                        ),
                    ]
                )
                command = "ssh"
                args = ssh_args
            else:
                env, cwd = _prepare_local_codex_home(config["workspace"], session_id or ctx.run_id, env, mcp_toml)
            async for result in _run_cli_process(
                ctx,
                command,
                args,
                cwd=cwd,
                env=env,
                timeout=config["timeout"],
                streaming=config["streaming"],
                resume_session_id=session_id,
                prompt=prompt,
                agent_cwd=config["workspace"],
                initial_stdin=initial_stdin,
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
        session_id: str,
    ) -> typing.AsyncGenerator[AgentRunResult, None]:
        hub = get_agent_runtime_daemon_hub("codex", error_code_prefix="codex")
        if not hub.is_running:
            await hub.start(
                host=config["daemon_hub"]["host"],
                port=config["daemon_hub"]["port"],
                token=config["daemon_hub"]["token"],
            )
        tools = AgentRunExternalTools(self.get_run_api(ctx), ctx) if config["langbot_assets_enabled"] else None
        await hub.wait_for_daemon(config["daemon_id"], config["daemon_connect_timeout"])
        payload = {
            "prompt": prompt,
            "session_id": session_id,
            "run_id": ctx.run_id,
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


class NativeCodexDaemon(AgentRuntimeDaemonClient):
    async def run_job(self, job_id: str, payload: dict[str, typing.Any]) -> None:
        proxy = None
        config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
        try:
            mcp_servers: list[AgentMCPServerConfig] = []
            if config.get("langbot_assets_enabled", True):
                proxy = self.create_mcp_proxy(job_id, request_timeout=float(config.get("timeout") or 300.0))
                proxy.start()
                mcp_servers.append(proxy.mcp_server())
            mcp_toml = _mcp_config_toml(_mcp_servers_config(mcp_servers, list(config.get("mcp_servers") or [])))
            argv = [*_parse_args(config.get("command") or "codex"), "app-server", "--listen", "stdio://", *list(config.get("args") or [])]
            session_id = str(payload.get("session_id") or "")
            env = {**os.environ, **{str(k): str(v) for k, v in dict(config.get("env") or {}).items()}}
            env, cwd = _prepare_local_codex_home(
                str(config.get("workspace") or os.getcwd()),
                session_id or str(payload.get("run_id") or job_id),
                env,
                mcp_toml,
            )
            try:
                async for event in _run_cli_process_events(
                    argv[0],
                    argv[1:],
                    cwd=cwd,
                    env=env,
                    timeout=float(config.get("timeout") or 300.0),
                    streaming=bool(config.get("streaming", True)),
                    resume_session_id=session_id,
                    prompt=str(payload.get("prompt") or ""),
                    agent_cwd=cwd,
                    initial_stdin=b"",
                ):
                    await self.emit_event(job_id, event)
            except NativeCliError as exc:
                await self.emit_event(
                    job_id,
                    {
                        "type": "run.failed",
                        "data": {"error": exc.message, "code": exc.code, "retryable": exc.retryable},
                    },
                )
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
    resume_session_id: str,
    prompt: str,
    agent_cwd: str,
    initial_stdin: bytes = b"",
) -> typing.AsyncGenerator[AgentRunResult, None]:
    try:
        async for event in _run_cli_process_events(
            command,
            args,
            cwd=cwd,
            env=env,
            timeout=timeout,
            streaming=streaming,
            resume_session_id=resume_session_id,
            prompt=prompt,
            agent_cwd=agent_cwd,
            initial_stdin=initial_stdin,
        ):
            event.setdefault("run_id", ctx.run_id)
            yield AgentRunResult.model_validate(event)
    except NativeCliError as exc:
        yield AgentRunResult.run_failed(ctx.run_id, error=exc.message, code=exc.code, retryable=exc.retryable)


def _extract_nested_string(data: dict[str, typing.Any], *keys: str) -> str:
    current: typing.Any = data
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return str(current) if isinstance(current, str) else ""


def _extract_thread_id(result: typing.Any) -> str:
    if not isinstance(result, dict):
        return ""
    for key in ("threadId", "thread_id", "id"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    thread = result.get("thread")
    if isinstance(thread, dict):
        for key in ("id", "threadId", "thread_id"):
            value = thread.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


class _CodexAppServerClient:
    def __init__(self, process: asyncio.subprocess.Process, *, streaming: bool) -> None:
        self.process = process
        self.streaming = streaming
        self.next_id = 0
        self.pending: dict[int, asyncio.Future[typing.Any]] = {}
        self.events: asyncio.Queue[dict[str, typing.Any]] = asyncio.Queue()
        self.turn_done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.thread_id = ""
        self.turn_started = False
        self.sequence = 0
        self.final_parts: list[str] = []
        self.final_error = ""
        self.seen_agent_message_item_ids: set[str] = set()
        self.final_chunk_emitted = False

    async def _write_json(self, payload: dict[str, typing.Any]) -> None:
        assert self.process.stdin is not None
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    async def request(self, method: str, params: dict[str, typing.Any] | None = None) -> typing.Any:
        self.next_id += 1
        request_id = self.next_id
        future: asyncio.Future[typing.Any] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        await self._write_json({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        return await future

    async def notify(self, method: str) -> None:
        await self._write_json({"jsonrpc": "2.0", "method": method})

    async def respond(self, request_id: typing.Any, result: typing.Any) -> None:
        await self._write_json({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def respond_error(self, request_id: typing.Any, message: str) -> None:
        await self._write_json({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": message}})

    async def initialize(self) -> None:
        await self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "langbot-codex-agent",
                    "title": "LangBot Codex Agent",
                "version": "0.1.7",
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        await self.notify("initialized")

    async def start_or_resume_thread(self, resume_session_id: str, cwd: str) -> str:
        if resume_session_id:
            try:
                result = await self.request(
                    "thread/resume",
                    {
                        "threadId": resume_session_id,
                        "cwd": cwd,
                        "model": None,
                        "developerInstructions": None,
                    },
                )
                thread_id = _extract_thread_id(result)
                if thread_id:
                    self.thread_id = thread_id
                    return thread_id
            except Exception as exc:
                logger.debug(
                    "Failed to resume Codex thread; starting a new thread: thread_id=%s error=%s",
                    resume_session_id,
                    exc,
                    exc_info=True,
                )

        result = await self.request(
            "thread/start",
            {
                "model": None,
                "modelProvider": None,
                "profile": None,
                "cwd": cwd,
                "approvalPolicy": None,
                "sandbox": None,
                "config": None,
                "baseInstructions": None,
                "developerInstructions": None,
                "compactPrompt": None,
                "includeApplyPatchTool": None,
                "experimentalRawEvents": False,
                "persistExtendedHistory": True,
            },
        )
        thread_id = _extract_thread_id(result)
        if not thread_id:
            raise NativeCliError("Codex thread/start returned no thread ID", code="codex.thread_start_failed")
        self.thread_id = thread_id
        return thread_id

    async def start_turn(self, thread_id: str, prompt: str) -> None:
        await self.request("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]})

    async def read_stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    await self.handle_line(text)
        finally:
            error = NativeCliError("Codex app-server exited before completing the turn", code="codex.process_exited")
            for request_id, future in list(self.pending.items()):
                if not future.done():
                    future.set_exception(error)
                self.pending.pop(request_id, None)
            if self.turn_started and not self.turn_done.done():
                self.turn_done.set_exception(error)

    async def handle_line(self, line: str) -> None:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(raw, dict):
            return
        if "id" in raw and ("result" in raw or "error" in raw):
            self.handle_response(raw)
            return
        if "id" in raw and "method" in raw:
            await self.handle_server_request(raw)
            return
        if "method" in raw:
            await self.handle_notification(raw)

    def handle_response(self, raw: dict[str, typing.Any]) -> None:
        request_id = raw.get("id")
        future = self.pending.pop(request_id, None) if isinstance(request_id, int) else None
        if future is None or future.done():
            return
        if "error" in raw:
            error = raw.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or error)
            else:
                message = str(error)
            future.set_exception(NativeCliError(message, code="codex.rpc_error"))
            return
        future.set_result(raw.get("result"))

    async def handle_server_request(self, raw: dict[str, typing.Any]) -> None:
        request_id = raw.get("id")
        method = str(raw.get("method") or "")
        if method in {"item/commandExecution/requestApproval", "execCommandApproval", "item/fileChange/requestApproval", "applyPatchApproval"}:
            await self.respond(request_id, {"decision": "accept"})
        elif method == "mcpServer/elicitation/request":
            await self.respond(request_id, {"action": "accept", "content": None, "_meta": None})
        else:
            await self.respond_error(request_id, f"unhandled server request: {method}")

    async def handle_notification(self, raw: dict[str, typing.Any]) -> None:
        method = str(raw.get("method") or "")
        params = raw.get("params")
        if not isinstance(params, dict):
            params = {}
        if method == "codex/event" or method.startswith("codex/event/"):
            msg = params.get("msg")
            if isinstance(msg, dict):
                await self.handle_legacy_event(msg)
            return
        if self.thread_id:
            event_thread_id = params.get("threadId")
            if isinstance(event_thread_id, str) and event_thread_id != self.thread_id:
                return
        await self.handle_raw_notification(method, params)

    async def handle_legacy_event(self, msg: dict[str, typing.Any]) -> None:
        msg_type = str(msg.get("type") or "")
        if msg_type == "task_started":
            self.turn_started = True
        elif msg_type == "agent_message":
            text = msg.get("message")
            if isinstance(text, str) and text:
                await self.emit_text(text)
        elif msg_type == "task_complete":
            self.finish_turn()
        elif msg_type == "turn_aborted":
            self.finish_turn(error="turn was aborted")

    async def handle_raw_notification(self, method: str, params: dict[str, typing.Any]) -> None:
        if method == "turn/started":
            self.turn_started = True
            return
        if method == "turn/completed":
            status = _extract_nested_string(params, "turn", "status")
            if status == "failed":
                self.finish_turn(error=_extract_nested_string(params, "turn", "error", "message") or "codex turn failed")
            else:
                self.finish_turn()
            return
        if method == "thread/status/changed" and self.turn_started:
            if _extract_nested_string(params, "status", "type") == "idle":
                self.finish_turn()
            return
        if method == "error":
            will_retry = bool(params.get("willRetry"))
            message = _extract_nested_string(params, "error", "message") or _extract_nested_string(params, "message")
            if message and not will_retry:
                self.finish_turn(error=message)
            return
        if method.startswith("item/"):
            await self.handle_item_notification(method, params)

    async def handle_item_notification(self, method: str, params: dict[str, typing.Any]) -> None:
        item = params.get("item")
        if not isinstance(item, dict):
            return
        item_type = str(item.get("type") or "")
        if method == "item/completed" and item_type == "agentMessage":
            item_id = str(item.get("id") or "")
            if item_id and item_id in self.seen_agent_message_item_ids:
                return
            if item_id:
                self.seen_agent_message_item_ids.add(item_id)
            text = item.get("text")
            is_final = item.get("phase") == "final_answer"
            if isinstance(text, str) and text:
                await self.emit_text(text, is_final=is_final)
            if is_final and self.turn_started:
                self.finish_turn()

    async def emit_text(self, text: str, *, is_final: bool = False) -> None:
        if is_final and self.final_chunk_emitted:
            return
        duplicate = bool(self.final_parts and self.final_parts[-1] == text)
        if duplicate and not is_final:
            return
        if not duplicate:
            self.final_parts.append(text)
        if not self.streaming:
            return
        all_content = "".join(self.final_parts)
        self.sequence += 1
        chunk = {
            "role": "assistant",
            "content": all_content if is_final else text,
            "all_content": all_content,
            "msg_sequence": self.sequence,
        }
        if is_final:
            chunk["is_final"] = True
            self.final_chunk_emitted = True
        await self.events.put(
            {
                "type": "message.delta",
                "sequence": self.sequence,
                "data": {"chunk": chunk},
            }
        )

    def finish_turn(self, error: str = "") -> None:
        if error and not self.final_error:
            self.final_error = error
        if not self.turn_done.done():
            if error:
                self.turn_done.set_exception(NativeCliError(error, code="codex.turn_failed"))
            else:
                self.turn_done.set_result(None)

    async def drain_events_until_done(self) -> typing.AsyncGenerator[dict[str, typing.Any], None]:
        while True:
            if self.turn_done.done():
                while not self.events.empty():
                    yield await self.events.get()
                await self.turn_done
                return
            get_task = asyncio.create_task(self.events.get())
            done, _ = await asyncio.wait({get_task, self.turn_done}, return_when=asyncio.FIRST_COMPLETED)
            if get_task in done:
                yield get_task.result()
            else:
                get_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await get_task


async def _run_cli_process_events(
    command: str,
    args: list[str],
    *,
    cwd: str | None,
    env: dict[str, str],
    timeout: float,
    streaming: bool,
    resume_session_id: str,
    prompt: str,
    agent_cwd: str,
    initial_stdin: bytes = b"",
) -> typing.AsyncGenerator[dict[str, typing.Any], None]:
    try:
        process = await asyncio.create_subprocess_exec(
            command,
            *args,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise NativeCliError(f"Codex command not found: {command}", code="codex.command_not_found") from exc
    except PermissionError as exc:
        raise NativeCliError(f"Codex command is not executable: {command}", code="codex.permission_denied") from exc
    except OSError as exc:
        raise NativeCliError(f"Failed to start Codex command: {exc}", code="codex.start_failed") from exc
    assert process.stdout is not None
    assert process.stdin is not None
    assert process.stderr is not None
    client = _CodexAppServerClient(process, streaming=streaming)
    reader_task = asyncio.create_task(client.read_stdout())
    stderr_task = asyncio.create_task(process.stderr.read())
    result_sequence = 0
    try:
        if initial_stdin:
            process.stdin.write(initial_stdin)
            await process.stdin.drain()
        async with asyncio.timeout(timeout):
            await client.initialize()
            thread_id = await client.start_or_resume_thread(resume_session_id, agent_cwd)
            if thread_id and thread_id != resume_session_id:
                result_sequence += 1
                yield {
                    "type": "state.updated",
                    "sequence": result_sequence,
                    "data": {"key": SESSION_STATE_KEY, "value": thread_id, "scope": "conversation"},
                }
            await client.start_turn(thread_id, prompt)
            async for event in client.drain_events_until_done():
                result_sequence += 1
                event["sequence"] = result_sequence
                yield event
        await _shutdown_app_server(process, reader_task)
        stderr = _redact_secrets((await stderr_task).decode("utf-8", errors="replace").strip())
        if process.returncode not in (0, None):
            raise NativeCliError(stderr or f"Codex app-server exited with status {process.returncode}", code="codex.process_failed")
        if client.final_error:
            raise NativeCliError(client.final_error, code="codex.turn_failed")
        final_text = "".join(client.final_parts).strip()
        if not final_text:
            raise NativeCliError("Codex returned no assistant text", code="codex.empty_response")
        if streaming:
            if not client.final_chunk_emitted:
                client.sequence += 1
                result_sequence += 1
                yield {
                    "type": "message.delta",
                    "sequence": result_sequence,
                    "data": {
                        "chunk": {
                            "role": "assistant",
                            "content": final_text,
                            "all_content": final_text,
                            "is_final": True,
                            "msg_sequence": client.sequence,
                        }
                    },
                }
        result_sequence += 1
        final_message = {"role": "assistant", "content": final_text}
        yield {
            "type": "message.completed",
            "sequence": result_sequence,
            "data": {"message": final_message},
        }
        result_sequence += 1
        yield {"type": "run.completed", "sequence": result_sequence, "data": {"finish_reason": "stop", "message": final_message}}
    except TimeoutError as exc:
        process.kill()
        raise NativeCliError("Codex app-server run timed out", code="codex.timeout", retryable=True) from exc
    finally:
        if process.returncode is None:
            process.kill()
        if not stderr_task.done():
            stderr_task.cancel()
        if not reader_task.done():
            reader_task.cancel()


async def _shutdown_app_server(process: asyncio.subprocess.Process, reader_task: asyncio.Task[None]) -> None:
    if process.stdin and not process.stdin.is_closing():
        process.stdin.close()
        try:
            await process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        process.kill()
        await process.wait()
    with contextlib.suppress(asyncio.CancelledError):
        await reader_task
