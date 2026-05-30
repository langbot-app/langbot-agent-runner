"""Codex Agent default runner implementation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import re
import shlex
import typing

from langbot_plugin.api.agent_tools import LANGBOT_AGENT_MCP_SERVER_NAME, merge_mcp_server_config
from langbot_plugin.api.definition.components.agent_runner.runner import AgentRunner
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentRunContext,
    AgentRunnerCapabilities,
    AgentRunResult,
    AgentRunResultType,
)
from langbot_plugin.api.entities.builtin.provider.message import Message

logger = logging.getLogger(__name__)

DRY_RUN_ENV_NAMES = (
    "LANGBOT_CODEX_AGENT_DRY_RUN",
    "CODEX_AGENT_DRY_RUN",
)

DEFAULT_CONTEXT_DIRECTORY = ".langbot/agent-runner"
LANGBOT_AGENT_MCP_AUTO_APPROVE_TOOLS = (
    "langbot_call_tool",
    "langbot_get_current_event",
    "langbot_history_page",
    "langbot_retrieve_knowledge",
)


class PreparedInjection:
    """Prepared external-harness context for one Codex run."""

    def __init__(self) -> None:
        self.prompt_prefix = ""
        self.mcp_config_path = ""
        self.context_json_path = ""
        self.context_markdown_path = ""
        self.skills_directory = ""
        self.mcp_config_data: dict[str, typing.Any] | None = None


def _to_bool(value: typing.Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _parse_args(value: typing.Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return shlex.split(value)
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _parse_positive_int(value: typing.Any, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _safe_name(value: typing.Any, fallback: str = "item") -> str:
    text = str(value or fallback).strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip(".-")
    return (text or fallback)[:96]


def _dump_jsonable(value: typing.Any) -> typing.Any:
    if value is None:
        return None
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _dump_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _loads_json_config(value: typing.Any, field_name: str) -> typing.Any:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError(f"{field_name} must be valid JSON: {e}") from e
    return value


def _parse_config_overrides(value: typing.Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return shlex.split(stripped)
        return _parse_config_overrides(parsed)
    if isinstance(value, dict):
        return [f"{key}={_toml_literal(item)}" for key, item in value.items()]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _toml_literal(value: typing.Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{key} = {_toml_literal(item)}" for key, item in value.items()) + " }"
    return json.dumps(value, ensure_ascii=False)


def _resolve_under_workdir(working_directory: str, value: str) -> pathlib.Path:
    path = pathlib.Path(value).expanduser()
    if not path.is_absolute():
        path = pathlib.Path(working_directory) / path
    return path


def _safe_child_path(base_dir: pathlib.Path, relative_path: typing.Any) -> pathlib.Path | None:
    path = pathlib.Path(str(relative_path or "")).expanduser()
    if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
        return None
    return base_dir / path


def _add_langbot_mcp_tool_approvals(data: dict[str, typing.Any]) -> None:
    servers = data.get("mcpServers") or data.get("mcp_servers")
    if not isinstance(servers, dict):
        return

    server = servers.get(LANGBOT_AGENT_MCP_SERVER_NAME)
    if not isinstance(server, dict):
        return

    tools = server.setdefault("tools", {})
    if not isinstance(tools, dict):
        tools = {}
        server["tools"] = tools

    for tool_name in LANGBOT_AGENT_MCP_AUTO_APPROVE_TOOLS:
        tool_config = tools.setdefault(tool_name, {})
        if isinstance(tool_config, dict):
            tool_config.setdefault("approval_mode", "approve")


class DefaultAgentRunner(AgentRunner):
    """Minimal AgentRunner for the local Codex CLI."""

    @classmethod
    def get_capabilities(cls) -> AgentRunnerCapabilities:
        """Get runner capabilities."""
        return AgentRunnerCapabilities(
            streaming=False,
            stateful_session=True,
        )

    def _get_config(self, ctx: AgentRunContext) -> dict[str, typing.Any]:
        config = ctx.config or {}
        return {
            "cli_command": config.get("cli-command", "codex") or "codex",
            "extra_args": _parse_args(config.get("extra-args", "")),
            "working_directory": config.get("working-directory", ""),
            "inject_context": _to_bool(config.get("inject-context", True)),
            "context_directory": config.get("context-directory", DEFAULT_CONTEXT_DIRECTORY) or DEFAULT_CONTEXT_DIRECTORY,
            "enable_langbot_mcp": _to_bool(config.get("enable-langbot-mcp", False)),
            "inject_skills": _to_bool(config.get("inject-skills", True)),
            "skills_json": config.get("skills-json", ""),
            "mcp_config_json": config.get("mcp-config-json", ""),
            "mcp_config_file": config.get("mcp-config-file", ""),
            "model": config.get("model", ""),
            "profile": config.get("profile", ""),
            "approval_policy": config.get("approval-policy", "never") or "",
            "sandbox": config.get("sandbox", "read-only") or "",
            "output_format": config.get("output-format", "json") or "json",
            "skip_git_repo_check": _to_bool(config.get("skip-git-repo-check", True)),
            "ephemeral": _to_bool(config.get("ephemeral")),
            "ignore_rules": _to_bool(config.get("ignore-rules")),
            "config_overrides": _parse_config_overrides(config.get("config-overrides", "")),
            "environment_json": config.get("environment-json", ""),
            "resume": _to_bool(config.get("resume", True)),
            "timeout": float(config.get("timeout", 300) or 300),
            "dry_run": _to_bool(config.get("dry-run"))
            or any(_to_bool(os.getenv(name)) for name in DRY_RUN_ENV_NAMES),
            "mock_response": config.get("mock-response", ""),
        }

    def _get_resume_session_id(self, ctx: AgentRunContext) -> str:
        return str(ctx.state.conversation.get("external.session_id") or "")

    def _get_working_directory(self, ctx: AgentRunContext, config: dict[str, typing.Any]) -> str:
        configured = str(config["working_directory"] or "").strip()
        if configured:
            return os.path.expanduser(configured)

        stored = str(ctx.state.conversation.get("external.working_directory") or "").strip()
        if stored:
            return os.path.expanduser(stored)

        return os.getcwd()

    def _build_command(
        self,
        config: dict[str, typing.Any],
        resume_session_id: str = "",
        output_last_message_path: str = "",
        mcp_config_overrides: list[str] | None = None,
    ) -> list[str]:
        command = shlex.split(str(config["cli_command"]))
        if not command:
            command = ["codex"]

        if command[-1] != "exec":
            command.append("exec")

        use_resume = bool(config["resume"] and resume_session_id)
        if use_resume:
            command.append("resume")

        if config["output_format"] == "json":
            command.append("--json")
        if output_last_message_path:
            command.extend(["--output-last-message", output_last_message_path])
        if config["model"]:
            command.extend(["--model", str(config["model"])])
        if not use_resume and config["profile"]:
            command.extend(["--profile", str(config["profile"])])
        if not use_resume and config["sandbox"]:
            command.extend(["--sandbox", str(config["sandbox"])])
        if not use_resume and config["working_directory"]:
            command.extend(["--cd", str(config["working_directory"])])
        if config["skip_git_repo_check"]:
            command.append("--skip-git-repo-check")
        if config["ephemeral"]:
            command.append("--ephemeral")
        if config["ignore_rules"]:
            command.append("--ignore-rules")
        if config["approval_policy"]:
            command.extend(["--config", f"approval_policy={_toml_literal(str(config['approval_policy']))}"])

        for item in [*config["config_overrides"], *(mcp_config_overrides or [])]:
            command.extend(["--config", item])

        command.extend(config["extra_args"])
        if use_resume:
            command.extend([resume_session_id, "-"])
        else:
            command.append("-")
        return command

    def _get_input_text(self, ctx: AgentRunContext) -> str:
        if ctx.input is None:
            return ""
        to_text = getattr(ctx.input, "to_text", None)
        if callable(to_text):
            return to_text()
        text = getattr(ctx.input, "text", "")
        return text or ""

    def _build_stdin(self, input_text: str, injection: PreparedInjection | None = None) -> bytes:
        if injection and injection.prompt_prefix:
            input_text = f"{injection.prompt_prefix}\n\nUser event input:\n{input_text}"

        return input_text.encode("utf-8")

    def _run_context_directory(self, working_directory: str, ctx: AgentRunContext, config: dict[str, typing.Any]) -> pathlib.Path:
        base_dir = _resolve_under_workdir(working_directory, str(config["context_directory"]))
        return base_dir / _safe_name(ctx.run_id, "run")

    def _build_context_payload(
        self,
        ctx: AgentRunContext,
        input_text: str,
        working_directory: str,
    ) -> dict[str, typing.Any]:
        return {
            "schema": "langbot.agent_runner.external_harness_context.v1",
            "run_id": ctx.run_id,
            "working_directory": working_directory,
            "trigger": _dump_jsonable(ctx.trigger),
            "event": _dump_jsonable(ctx.event),
            "conversation": _dump_jsonable(ctx.conversation),
            "actor": _dump_jsonable(ctx.actor),
            "subject": _dump_jsonable(ctx.subject),
            "input": {
                "text": input_text,
                "attachments": _dump_jsonable(getattr(ctx.input, "attachments", [])),
                "contents": _dump_jsonable(getattr(ctx.input, "contents", [])),
            },
            "delivery": _dump_jsonable(ctx.delivery),
            "resources": _dump_jsonable(ctx.resources),
            "context": _dump_jsonable(ctx.context),
            "state": _dump_jsonable(ctx.state),
            "runtime": _dump_jsonable(ctx.runtime),
            "bootstrap": _dump_jsonable(ctx.bootstrap),
            "adapter": _dump_jsonable(ctx.adapter),
            "metadata": _dump_jsonable(ctx.metadata),
        }

    def _build_context_markdown(self, payload: dict[str, typing.Any]) -> str:
        event = payload.get("event") or {}
        actor = payload.get("actor") or {}
        input_data = payload.get("input") or {}
        resources = payload.get("resources") or {}
        context = payload.get("context") or {}

        return "\n".join(
            [
                "# LangBot Run Context",
                "",
                f"- Run ID: `{payload.get('run_id', '')}`",
                f"- Event: `{event.get('event_type', '')}` from `{event.get('source', '')}`",
                f"- Actor: `{actor.get('actor_type', '')}:{actor.get('actor_id', '')}`",
                f"- Working directory: `{payload.get('working_directory', '')}`",
                "",
                "## Current Input",
                "",
                str(input_data.get("text") or ""),
                "",
                "## Authorized Resources",
                "",
                "```json",
                json.dumps(resources, ensure_ascii=False, indent=2),
                "```",
                "",
                "## Context Access",
                "",
                "```json",
                json.dumps(context, ensure_ascii=False, indent=2),
                "```",
                "",
                "## Full Context",
                "",
                "See `agent-context.json` in the same directory.",
                "",
            ]
        )

    def _write_context_files(
        self,
        ctx: AgentRunContext,
        input_text: str,
        working_directory: str,
        run_dir: pathlib.Path,
    ) -> tuple[str, str]:
        payload = self._build_context_payload(ctx, input_text, working_directory)
        run_dir.mkdir(parents=True, exist_ok=True)

        json_path = run_dir / "agent-context.json"
        markdown_path = run_dir / "LANGBOT_CONTEXT.md"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(self._build_context_markdown(payload), encoding="utf-8")
        return str(json_path), str(markdown_path)

    def _write_skills(self, run_dir: pathlib.Path, config: dict[str, typing.Any]) -> str:
        if not config["inject_skills"]:
            return ""

        data = _loads_json_config(config["skills_json"], "skills-json")
        if not data:
            return ""

        skills = data.get("skills", data) if isinstance(data, dict) else data
        if not isinstance(skills, list):
            raise ValueError("skills-json must be a JSON array or an object with a skills array")

        skills_dir = run_dir / "codex-skills"
        for index, skill in enumerate(skills):
            if not isinstance(skill, dict):
                raise ValueError("each skills-json entry must be an object")

            skill_name = _safe_name(skill.get("name") or skill.get("title"), f"skill-{index + 1}")
            skill_dir = skills_dir / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)

            content = skill.get("content")
            if content is None:
                content = skill.get("markdown", "")
            (skill_dir / "SKILL.md").write_text(str(content), encoding="utf-8")

            files = skill.get("files") or []
            if isinstance(files, dict):
                file_items = [{"path": path, "content": file_content} for path, file_content in files.items()]
            elif isinstance(files, list):
                file_items = files
            else:
                raise ValueError("skills-json files must be an object or array")

            for file_item in file_items:
                if not isinstance(file_item, dict):
                    raise ValueError("skills-json file entries must be objects")
                path = _safe_child_path(skill_dir, file_item.get("path"))
                if path is None:
                    raise ValueError("skills-json file paths must be relative and stay inside the skill directory")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(file_item.get("content", "")), encoding="utf-8")

        return str(skills_dir)

    def _write_mcp_config(
        self,
        working_directory: str,
        run_dir: pathlib.Path,
        config: dict[str, typing.Any],
        langbot_mcp_server: dict[str, typing.Any] | None = None,
    ) -> tuple[str, dict[str, typing.Any] | None]:
        configured_file = str(config["mcp_config_file"] or "").strip()
        if configured_file and not langbot_mcp_server:
            return str(_resolve_under_workdir(working_directory, configured_file)), None

        if configured_file:
            configured_path = _resolve_under_workdir(working_directory, configured_file)
            data = json.loads(configured_path.read_text(encoding="utf-8"))
        else:
            data = _loads_json_config(config["mcp_config_json"], "mcp-config-json")
        if not data:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("mcp-config-json must be a JSON object")

        if langbot_mcp_server:
            data = merge_mcp_server_config(data, langbot_mcp_server)
            _add_langbot_mcp_tool_approvals(data)
        if not data:
            return "", None

        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "mcp.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path), data

    def _build_mcp_config_overrides(self, data: dict[str, typing.Any] | None) -> list[str]:
        if not data or not isinstance(data, dict):
            return []

        servers = data.get("mcpServers") or data.get("mcp_servers") or {}
        if not isinstance(servers, dict):
            return []

        overrides: list[str] = []
        for name, server in servers.items():
            if not isinstance(server, dict):
                continue
            safe_name = _safe_name(name, "server").replace("-", "_")
            for key in ("command", "args", "env", "url", "headers"):
                if key in server:
                    overrides.append(f"mcp_servers.{safe_name}.{key}={_toml_literal(server[key])}")
            tools = server.get("tools") or {}
            if isinstance(tools, dict):
                for tool_name, tool_config in tools.items():
                    if not isinstance(tool_config, dict):
                        continue
                    safe_tool_name = _safe_name(tool_name, "tool").replace("-", "_")
                    for key in ("approval_mode",):
                        if key in tool_config:
                            overrides.append(
                                f"mcp_servers.{safe_name}.tools.{safe_tool_name}.{key}="
                                f"{_toml_literal(tool_config[key])}"
                            )
        return overrides

    def _prepare_injection(
        self,
        ctx: AgentRunContext,
        input_text: str,
        working_directory: str,
        config: dict[str, typing.Any],
        langbot_mcp_server: dict[str, typing.Any] | None = None,
    ) -> PreparedInjection:
        run_dir = self._run_context_directory(working_directory, ctx, config)
        injection = PreparedInjection()

        if config["inject_context"]:
            injection.context_json_path, injection.context_markdown_path = self._write_context_files(
                ctx,
                input_text,
                working_directory,
                run_dir,
            )

        injection.skills_directory = self._write_skills(run_dir, config)
        injection.mcp_config_path, injection.mcp_config_data = self._write_mcp_config(
            working_directory,
            run_dir,
            config,
            langbot_mcp_server,
        )

        prefix_lines = []
        if injection.context_json_path:
            prefix_lines.extend(
                [
                    "LangBot prepared read-only run context for this event.",
                    f"- Context JSON: {injection.context_json_path}",
                    f"- Context Markdown: {injection.context_markdown_path}",
                ]
            )
        if injection.skills_directory:
            prefix_lines.append(f"- Codex skills directory: {injection.skills_directory}")
        if injection.mcp_config_path:
            prefix_lines.append(f"- Codex MCP config: {injection.mcp_config_path}")
        if langbot_mcp_server:
            prefix_lines.append(f"- LangBot MCP server: {LANGBOT_AGENT_MCP_SERVER_NAME}")

        if prefix_lines:
            prefix_lines.append("Use these files only as scoped context for the current LangBot event.")
            injection.prompt_prefix = "\n".join(prefix_lines)

        return injection

    async def _run_cli(
        self,
        command: list[str],
        stdin: bytes,
        timeout: float,
        working_directory: str,
        config: dict[str, typing.Any],
    ) -> tuple[int, str, str]:
        env = self._build_subprocess_env(config)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_directory,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(stdin),
                timeout=timeout,
            )
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise

        return (
            process.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    def _build_subprocess_env(self, config: dict[str, typing.Any]) -> dict[str, str]:
        env = os.environ.copy()
        home = str(pathlib.Path.home())
        if home:
            env.setdefault("HOME", home)
            env.setdefault("USERPROFILE", home)

        path_entries = [
            str(pathlib.Path(home) / ".local" / "bin") if home else "",
            str(pathlib.Path(home) / ".npm-global" / "bin") if home else "",
            env.get("PATH", ""),
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]
        seen: set[str] = set()
        normalized_path = []
        for entry in path_entries:
            if entry and entry not in seen:
                normalized_path.append(entry)
                seen.add(entry)
        env["PATH"] = os.pathsep.join(normalized_path)

        extra_env = _loads_json_config(config.get("environment_json"), "environment-json") or {}
        if not isinstance(extra_env, dict):
            raise ValueError("environment-json must be a JSON object")
        for key, value in extra_env.items():
            key_text = str(key).strip()
            if not key_text or value is None:
                continue
            env[key_text] = str(value)
        return env

    def _parse_stdout(
        self,
        stdout: str,
        output_format: str,
        output_last_message_path: str = "",
    ) -> tuple[str, str, dict[str, typing.Any]]:
        file_content = ""
        if output_last_message_path:
            path = pathlib.Path(output_last_message_path)
            if path.exists():
                file_content = path.read_text(encoding="utf-8").strip()
        if output_format == "json":
            content, session_id, metadata = self._parse_jsonl_events(stdout)
            return file_content or content, session_id, metadata
        return file_content or stdout.strip(), "", {"output_format": output_format}

    def _parse_jsonl_events(self, stdout: str) -> tuple[str, str, dict[str, typing.Any]]:
        output_parts: list[str] = []
        session_id = ""
        usage: dict[str, typing.Any] = {}
        event_count = 0

        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_count += 1
            event_type = data.get("type")
            if event_type == "thread.started" and data.get("thread_id"):
                session_id = str(data["thread_id"])
            elif data.get("session_id"):
                session_id = str(data["session_id"])
            elif event_type == "item.completed":
                item = data.get("item") or {}
                if item.get("type") == "agent_message" and item.get("text"):
                    output_parts.append(str(item["text"]))
            elif event_type == "turn.completed":
                usage = data.get("usage") or {}

        metadata = {
            "output_format": "json",
            "usage": usage,
            "event_count": event_count,
        }
        return "".join(output_parts).strip(), session_id, metadata

    def _run_completed(
        self,
        ctx: AgentRunContext,
        session_id: str,
        working_directory: str,
        metadata: dict[str, typing.Any],
    ) -> AgentRunResult:
        data: dict[str, typing.Any] = {
            "finish_reason": "stop",
            "external": {
                "provider": "codex",
                "session_id": session_id or None,
                "working_directory": working_directory,
            },
            "codex": {k: v for k, v in metadata.items() if v is not None},
        }
        return AgentRunResult(
            run_id=ctx.run_id,
            type=AgentRunResultType.RUN_COMPLETED,
            data=data,
        )

    async def run(self, ctx: AgentRunContext) -> typing.AsyncGenerator[AgentRunResult, None]:
        """Run Codex CLI and return the final assistant message."""
        config = self._get_config(ctx)
        input_text = self._get_input_text(ctx)

        if config["dry_run"]:
            content = config["mock_response"] or f"[dry-run] {input_text}"
            yield AgentRunResult.message_completed(
                ctx.run_id,
                Message(role="assistant", content=content),
            )
            yield AgentRunResult.run_completed(ctx.run_id)
            return

        resume_session_id = self._get_resume_session_id(ctx)
        working_directory = self._get_working_directory(ctx, config)

        if not os.path.isdir(working_directory):
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"Codex working directory not found: {working_directory}",
                code="codex.working_directory_not_found",
            )
            return

        run_dir = self._run_context_directory(working_directory, ctx, config)
        bridge = None
        try:
            langbot_mcp_server = None
            if config["enable_langbot_mcp"]:
                bridge = self.create_external_mcp_bridge(ctx)
                bridge.start()
                langbot_mcp_server = bridge.mcp_server_config()
            injection = self._prepare_injection(
                ctx,
                input_text,
                working_directory,
                config,
                langbot_mcp_server,
            )
        except Exception as e:
            if bridge is not None:
                bridge.stop()
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"Codex context injection failed: {e}",
                code="codex.context_injection_error",
            )
            return

        run_dir.mkdir(parents=True, exist_ok=True)
        output_last_message_path = str(run_dir / "codex-last-message.txt")
        command = self._build_command(
            config,
            resume_session_id,
            output_last_message_path,
            self._build_mcp_config_overrides(injection.mcp_config_data),
        )
        stdin = self._build_stdin(input_text, injection)

        try:
            returncode, stdout, stderr = await self._run_cli(
                command,
                stdin,
                config["timeout"],
                working_directory,
                config,
            )
        except FileNotFoundError:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"Codex CLI command not found: {command[0]}",
                code="codex.command_not_found",
            )
            return
        except asyncio.TimeoutError:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"Codex CLI timed out after {config['timeout']} seconds",
                code="codex.timeout",
                retryable=True,
            )
            return
        except Exception as e:
            logger.exception("Codex runner unexpected error: %s", e)
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"Codex runner error: {e}",
                code="codex.unexpected_error",
            )
            return
        finally:
            if bridge is not None:
                bridge.stop()

        (run_dir / "codex-events.jsonl").write_text(stdout, encoding="utf-8")
        if stderr:
            (run_dir / "codex-stderr.log").write_text(stderr, encoding="utf-8")

        if returncode != 0:
            error = stderr.strip() or stdout.strip() or f"Codex CLI exited with code {returncode}"
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=error,
                code="codex.cli_error",
            )
            return

        content, session_id, metadata = self._parse_stdout(stdout, config["output_format"], output_last_message_path)
        if not content:
            error = stderr.strip() or stdout.strip() or "Codex CLI returned empty response"
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=error,
                code="codex.empty_response",
            )
            return

        yield AgentRunResult.message_completed(
            ctx.run_id,
            Message(role="assistant", content=content),
        )
        if session_id:
            yield AgentRunResult.state_updated(
                ctx.run_id,
                "external.session_id",
                session_id,
                scope="conversation",
            )
            yield AgentRunResult.state_updated(
                ctx.run_id,
                "external.working_directory",
                working_directory,
                scope="conversation",
            )
        yield self._run_completed(ctx, session_id, working_directory, metadata)
