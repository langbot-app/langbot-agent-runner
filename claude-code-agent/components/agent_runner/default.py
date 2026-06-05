"""Claude Code Agent default runner implementation."""

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
    "LANGBOT_CLAUDE_CODE_AGENT_DRY_RUN",
    "CLAUDE_CODE_AGENT_DRY_RUN",
)

DEFAULT_CONTEXT_DIRECTORY = ".langbot/agent-runner"


class PreparedInjection:
    """Prepared external-harness context for one Claude Code run."""

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


class DefaultAgentRunner(AgentRunner):
    """Minimal AgentRunner for the local Claude Code CLI."""

    @classmethod
    def get_capabilities(cls) -> AgentRunnerCapabilities:
        """Get runner capabilities."""
        return AgentRunnerCapabilities(
            streaming=False,
            stateful_session=True,
        )

    def _get_config(self, ctx: AgentRunContext) -> dict[str, typing.Any]:
        config = ctx.config or {}
        enable_langbot_mcp = _to_bool(config.get("enable-langbot-mcp", False))
        allowed_tools = _parse_args(config.get("allowed-tools", ""))
        if enable_langbot_mcp:
            tool_pattern = f"mcp__{LANGBOT_AGENT_MCP_SERVER_NAME}__*"
            if tool_pattern not in allowed_tools:
                allowed_tools.append(tool_pattern)
        return {
            "cli_command": config.get("cli-command", "claude") or "claude",
            "extra_args": _parse_args(config.get("extra-args", "")),
            "working_directory": config.get("working-directory", ""),
            "inject_context": _to_bool(config.get("inject-context", True)),
            "context_directory": config.get("context-directory", DEFAULT_CONTEXT_DIRECTORY) or DEFAULT_CONTEXT_DIRECTORY,
            "enable_langbot_mcp": enable_langbot_mcp,
            "inject_skills": _to_bool(config.get("inject-skills", True)),
            "skills_json": config.get("skills-json", ""),
            "mcp_config_json": config.get("mcp-config-json", ""),
            "mcp_config_file": config.get("mcp-config-file", ""),
            "strict_mcp_config": _to_bool(config.get("strict-mcp-config", True)),
            "model": config.get("model", ""),
            "output_format": config.get("output-format", "json") or "json",
            "input_format": config.get("input-format", "text") or "text",
            "setting_sources": config.get("setting-sources", ""),
            "permission_mode": config.get("permission-mode", "plan") or "",
            "tools": config["tools"] if "tools" in config else None,
            "allowed_tools": allowed_tools,
            "disallowed_tools": _parse_args(config.get("disallowed-tools", "AskUserQuestion")),
            "max_turns": _parse_positive_int(config.get("max-turns", 1), default=1),
            "verbose": _to_bool(config.get("verbose")),
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
        mcp_config_path: str = "",
    ) -> list[str]:
        command = shlex.split(str(config["cli_command"]))
        if not command:
            command = ["claude"]

        command.append("-p")
        if config["output_format"]:
            command.extend(["--output-format", str(config["output_format"])])
        if config["input_format"] and config["input_format"] != "text":
            command.extend(["--input-format", str(config["input_format"])])
        if config["output_format"] == "stream-json" or config["verbose"]:
            command.append("--verbose")
        if config["setting_sources"]:
            command.extend(["--setting-sources", str(config["setting_sources"])])
        if config["model"]:
            command.extend(["--model", str(config["model"])])
        if config["max_turns"]:
            command.extend(["--max-turns", str(config["max_turns"])])
        if config["permission_mode"]:
            command.extend(["--permission-mode", str(config["permission_mode"])])
        if config["tools"] is not None:
            command.extend(["--tools", str(config["tools"])])
        if config["allowed_tools"]:
            command.extend(["--allowedTools", *config["allowed_tools"]])
        if config["disallowed_tools"]:
            command.extend(["--disallowedTools", *config["disallowed_tools"]])
        if mcp_config_path:
            command.extend(["--mcp-config", mcp_config_path])
            if config["strict_mcp_config"]:
                command.append("--strict-mcp-config")
        if config["resume"] and resume_session_id:
            command.extend(["--resume", resume_session_id])

        return [*command, *config["extra_args"]]

    def _get_input_text(self, ctx: AgentRunContext) -> str:
        if ctx.input is None:
            return ""
        to_text = getattr(ctx.input, "to_text", None)
        if callable(to_text):
            return to_text()
        text = getattr(ctx.input, "text", "")
        return text or ""

    def _build_stdin(self, input_text: str, input_format: str, injection: PreparedInjection | None = None) -> bytes:
        if injection and injection.prompt_prefix:
            input_text = f"{injection.prompt_prefix}\n\nUser event input:\n{input_text}"

        if input_format != "stream-json":
            return input_text.encode("utf-8")

        payload = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": input_text,
                    }
                ],
            },
        }
        return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")

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

    def _write_skills(self, working_directory: str, config: dict[str, typing.Any]) -> str:
        if not config["inject_skills"]:
            return ""

        data = _loads_json_config(config["skills_json"], "skills-json")
        if not data:
            return ""

        skills = data.get("skills", data) if isinstance(data, dict) else data
        if not isinstance(skills, list):
            raise ValueError("skills-json must be a JSON array or an object with a skills array")

        skills_dir = pathlib.Path(working_directory) / ".claude" / "skills"
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
        if not data:
            return "", None

        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "mcp.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path), data

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

        injection.skills_directory = self._write_skills(working_directory, config)
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
            prefix_lines.append(f"- Claude Code skills directory: {injection.skills_directory}")
        if injection.mcp_config_path:
            prefix_lines.append(f"- Claude Code MCP config: {injection.mcp_config_path}")
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
    ) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_directory,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(stdin),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise

        return (
            process.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    def _parse_stdout(self, stdout: str, output_format: str) -> tuple[str, str, dict[str, typing.Any]]:
        if output_format == "stream-json":
            return self._parse_stream_json(stdout)
        if output_format == "json":
            return self._parse_json_result(stdout)
        return stdout.strip(), "", {"output_format": output_format}

    def _parse_json_result(self, stdout: str) -> tuple[str, str, dict[str, typing.Any]]:
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return stdout.strip(), "", {"output_format": "json", "raw_parse_error": "json_decode_error"}

        session_id = str(data.get("session_id") or "")
        metadata = {
            "output_format": "json",
            "subtype": data.get("subtype"),
            "is_error": data.get("is_error"),
            "stop_reason": data.get("stop_reason"),
            "terminal_reason": data.get("terminal_reason"),
            "usage": data.get("usage"),
            "model_usage": data.get("modelUsage"),
            "duration_ms": data.get("duration_ms"),
            "duration_api_ms": data.get("duration_api_ms"),
            "num_turns": data.get("num_turns"),
        }
        if data.get("is_error"):
            return "", session_id, metadata
        content = data.get("result")
        if content is None:
            content = data.get("content", "")
        return str(content).strip(), session_id, metadata

    def _parse_stream_json(self, stdout: str) -> tuple[str, str, dict[str, typing.Any]]:
        output_parts: list[str] = []
        final_result = ""
        session_id = ""
        final_event: dict[str, typing.Any] = {}
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
            if data.get("session_id"):
                session_id = str(data["session_id"])

            event_type = data.get("type")
            if event_type == "assistant":
                message = data.get("message") or {}
                for block in message.get("content", []):
                    if block.get("type") == "text" and block.get("text"):
                        output_parts.append(str(block["text"]))
            elif event_type == "result":
                final_result = str(data.get("result") or "")
                final_event = data

        content = final_result.strip() or "".join(output_parts).strip()
        metadata = {
            "output_format": "stream-json",
            "subtype": final_event.get("subtype"),
            "is_error": final_event.get("is_error"),
            "stop_reason": final_event.get("stop_reason"),
            "terminal_reason": final_event.get("terminal_reason"),
            "usage": final_event.get("usage"),
            "model_usage": final_event.get("modelUsage"),
            "duration_ms": final_event.get("duration_ms"),
            "duration_api_ms": final_event.get("duration_api_ms"),
            "num_turns": final_event.get("num_turns"),
            "event_count": event_count,
        }
        return content, session_id, metadata

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
                "provider": "claude_code",
                "session_id": session_id or None,
                "working_directory": working_directory,
            },
            "claude_code": {k: v for k, v in metadata.items() if v is not None},
        }
        return AgentRunResult(
            run_id=ctx.run_id,
            type=AgentRunResultType.RUN_COMPLETED,
            data=data,
        )

    async def run(self, ctx: AgentRunContext) -> typing.AsyncGenerator[AgentRunResult, None]:
        """Run Claude Code CLI and return stdout as a final assistant message."""
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
                error=f"Claude Code working directory not found: {working_directory}",
                code="claude_code.working_directory_not_found",
            )
            return

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
                error=f"Claude Code context injection failed: {e}",
                code="claude_code.context_injection_error",
            )
            return

        command = self._build_command(config, resume_session_id, injection.mcp_config_path)
        stdin = self._build_stdin(input_text, config["input_format"], injection)

        try:
            returncode, stdout, stderr = await self._run_cli(command, stdin, config["timeout"], working_directory)
        except FileNotFoundError:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"Claude Code CLI command not found: {command[0]}",
                code="claude_code.command_not_found",
            )
            return
        except asyncio.TimeoutError:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"Claude Code CLI timed out after {config['timeout']} seconds",
                code="claude_code.timeout",
                retryable=True,
            )
            return
        except Exception as e:
            logger.exception("Claude Code runner unexpected error: %s", e)
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"Claude Code runner error: {e}",
                code="claude_code.unexpected_error",
            )
            return
        finally:
            if bridge is not None:
                bridge.stop()

        if returncode != 0:
            error = stderr.strip() or stdout.strip() or f"Claude Code CLI exited with code {returncode}"
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=error,
                code="claude_code.cli_error",
            )
            return

        content, session_id, metadata = self._parse_stdout(stdout, config["output_format"])
        if not content:
            error = stderr.strip() or stdout.strip() or "Claude Code CLI returned empty stdout"
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=error,
                code="claude_code.empty_response",
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
