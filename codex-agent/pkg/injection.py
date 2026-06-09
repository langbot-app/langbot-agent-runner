"""Context and MCP injection for Codex runs."""

from __future__ import annotations

import json
import pathlib
import re
import typing

from langbot_plugin.api.agent_tools import LANGBOT_AGENT_MCP_SERVER_NAME, merge_mcp_server_config
from langbot_plugin.api.entities.builtin.agent_runner import AgentRunContext

from pkg import runner_utils

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
        self.mcp_config_data: dict[str, typing.Any] | None = None
        self.files: list[dict[str, typing.Any]] = []


def build_context_payload(
    ctx: AgentRunContext,
    input_text: str,
    working_directory: str,
) -> dict[str, typing.Any]:
    return {
        "schema": "langbot.agent_runner.external_harness_context.v1",
        "run_id": ctx.run_id,
        "working_directory": working_directory,
        "trigger": runner_utils.dump_jsonable(ctx.trigger),
        "event": runner_utils.dump_jsonable(ctx.event),
        "conversation": runner_utils.dump_jsonable(ctx.conversation),
        "actor": runner_utils.dump_jsonable(ctx.actor),
        "subject": runner_utils.dump_jsonable(ctx.subject),
        "input": {
            "text": input_text,
            "attachments": runner_utils.dump_jsonable(ctx.input.attachments),
            "contents": runner_utils.dump_jsonable(ctx.input.contents),
        },
        "delivery": runner_utils.dump_jsonable(ctx.delivery),
        "resources": runner_utils.dump_jsonable(ctx.resources),
        "context": runner_utils.dump_jsonable(ctx.context),
        "state": runner_utils.dump_jsonable(ctx.state),
        "runtime": runner_utils.dump_jsonable(ctx.runtime),
        "adapter": runner_utils.dump_jsonable(ctx.adapter),
        "metadata": runner_utils.dump_jsonable(ctx.metadata),
    }


def build_context_markdown(payload: dict[str, typing.Any]) -> str:
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


def run_context_directory(
    working_directory: str,
    ctx: AgentRunContext,
    config: dict[str, typing.Any],
) -> pathlib.Path:
    base_dir = runner_utils.resolve_context_directory(working_directory, config["context_directory"])
    return base_dir / runner_utils.safe_name(ctx.run_id, "run")


def write_context_files(
    ctx: AgentRunContext,
    input_text: str,
    working_directory: str,
    run_dir: pathlib.Path,
) -> tuple[str, str]:
    payload = build_context_payload(ctx, input_text, working_directory)
    run_dir.mkdir(parents=True, exist_ok=True)

    json_path = run_dir / "agent-context.json"
    markdown_path = run_dir / "LANGBOT_CONTEXT.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_context_markdown(payload), encoding="utf-8")
    return str(json_path), str(markdown_path)


def add_langbot_mcp_tool_approvals(data: dict[str, typing.Any]) -> None:
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


def write_langbot_mcp_config(
    run_dir: pathlib.Path,
    langbot_mcp_server: dict[str, typing.Any] | None,
) -> tuple[str, dict[str, typing.Any] | None]:
    if not langbot_mcp_server:
        return "", None

    data = merge_mcp_server_config({}, langbot_mcp_server)
    add_langbot_mcp_tool_approvals(data)

    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "mcp.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return str(path), data


def _codex_toml_key(value: str) -> str:
    if re.match(r"^[A-Za-z0-9_-]+$", value):
        return value
    return runner_utils.toml_literal(value)


def render_codex_mcp_servers_config(data: dict[str, typing.Any] | None) -> str:
    if not data or not isinstance(data, dict):
        return ""

    servers = data.get("mcpServers") or data.get("mcp_servers") or {}
    if not isinstance(servers, dict):
        return ""

    lines: list[str] = [
        "# BEGIN langbot-managed mcp_servers (do not edit; regenerated per run)",
    ]
    for name, server in servers.items():
        if not isinstance(server, dict):
            continue
        safe_server_name = runner_utils.safe_name(name, "server").replace("-", "_")
        lines.append("")
        lines.append(f"[mcp_servers.{_codex_toml_key(safe_server_name)}]")
        for key in ("command", "args", "env", "url", "headers"):
            if key in server:
                lines.append(f"{_codex_toml_key(key)} = {runner_utils.toml_literal(server[key])}")
        tools = server.get("tools") or {}
        if isinstance(tools, dict):
            for tool_name, tool_config in tools.items():
                if not isinstance(tool_config, dict):
                    continue
                safe_tool_name = runner_utils.safe_name(tool_name, "tool").replace("-", "_")
                for key in ("approval_mode",):
                    if key in tool_config:
                        lines.append("")
                        lines.append(
                            f"[mcp_servers.{_codex_toml_key(safe_server_name)}.tools."
                            f"{_codex_toml_key(safe_tool_name)}]"
                        )
                        lines.append(f"{_codex_toml_key(key)} = {runner_utils.toml_literal(tool_config[key])}")
    lines.append("")
    lines.append("# END langbot-managed mcp_servers")
    return "\n".join(lines) + "\n"


def _prefix_lines(
    injection: PreparedInjection,
    langbot_mcp_server: dict[str, typing.Any] | None = None,
) -> list[str]:
    prefix_lines = []
    if injection.context_json_path:
        prefix_lines.extend(
            [
                "LangBot prepared read-only run context for this event.",
                f"- Context JSON: {injection.context_json_path}",
                f"- Context Markdown: {injection.context_markdown_path}",
            ]
        )
    if injection.mcp_config_path:
        prefix_lines.append(f"- Codex MCP config: {injection.mcp_config_path}")
    if langbot_mcp_server:
        prefix_lines.append(f"- LangBot MCP server: {LANGBOT_AGENT_MCP_SERVER_NAME}")

    if prefix_lines:
        prefix_lines.append("Use these files only as scoped context for the current LangBot event.")
    return prefix_lines


def prepare_injection(
    ctx: AgentRunContext,
    input_text: str,
    working_directory: str,
    config: dict[str, typing.Any],
    langbot_mcp_server: dict[str, typing.Any] | None = None,
) -> PreparedInjection:
    run_dir = run_context_directory(working_directory, ctx, config)
    injection = PreparedInjection()

    if config["inject_context"]:
        injection.context_json_path, injection.context_markdown_path = write_context_files(
            ctx,
            input_text,
            working_directory,
            run_dir,
        )

    injection.mcp_config_path, injection.mcp_config_data = write_langbot_mcp_config(
        run_dir,
        langbot_mcp_server,
    )

    prefix_lines = _prefix_lines(injection, langbot_mcp_server)
    if prefix_lines:
        injection.prompt_prefix = "\n".join(prefix_lines)

    return injection


def prepare_remote_injection(
    ctx: AgentRunContext,
    input_text: str,
    workspace_key: str,
    config: dict[str, typing.Any],
) -> PreparedInjection:
    context_directory = runner_utils.safe_relative_posix_path(config["context_directory"])
    if not context_directory:
        raise ValueError("context-directory must be a relative path for remote execution")

    run_dir = pathlib.PurePosixPath(context_directory) / runner_utils.safe_name(ctx.run_id, "run")
    injection = PreparedInjection()
    files: list[dict[str, typing.Any]] = []

    if config["inject_context"]:
        payload = build_context_payload(ctx, input_text, workspace_key)
        context_json_path = str(run_dir / "agent-context.json")
        context_markdown_path = str(run_dir / "LANGBOT_CONTEXT.md")
        files.append(
            {
                "path": context_json_path,
                "content": json.dumps(payload, ensure_ascii=False, indent=2),
                "mode": 0o644,
            }
        )
        files.append(
            {
                "path": context_markdown_path,
                "content": build_context_markdown(payload),
                "mode": 0o644,
            }
        )
        injection.context_json_path = context_json_path
        injection.context_markdown_path = context_markdown_path

    prefix_lines = _prefix_lines(injection)
    if prefix_lines:
        injection.prompt_prefix = "\n".join(prefix_lines)

    injection.files = files
    return injection
