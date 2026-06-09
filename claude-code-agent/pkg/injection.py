"""Context injection for Claude Code runs."""

from __future__ import annotations

import json
import pathlib
import typing

from langbot_plugin.api.entities.builtin.agent_runner import AgentRunContext

from pkg.runner_utils import dump_jsonable, resolve_context_directory, safe_name, safe_relative_posix_path


class PreparedInjection:
    """Prepared external-harness context for one Claude Code run."""

    def __init__(self) -> None:
        self.prompt_prefix = ""
        self.context_json_path = ""
        self.context_markdown_path = ""
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
        "trigger": dump_jsonable(ctx.trigger),
        "event": dump_jsonable(ctx.event),
        "conversation": dump_jsonable(ctx.conversation),
        "actor": dump_jsonable(ctx.actor),
        "subject": dump_jsonable(ctx.subject),
        "input": {
            "text": input_text,
            "attachments": dump_jsonable(ctx.input.attachments),
            "contents": dump_jsonable(ctx.input.contents),
        },
        "delivery": dump_jsonable(ctx.delivery),
        "resources": dump_jsonable(ctx.resources),
        "context": dump_jsonable(ctx.context),
        "state": dump_jsonable(ctx.state),
        "runtime": dump_jsonable(ctx.runtime),
        "adapter": dump_jsonable(ctx.adapter),
        "metadata": dump_jsonable(ctx.metadata),
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


def run_context_directory(working_directory: str, ctx: AgentRunContext, config: dict[str, typing.Any]) -> pathlib.Path:
    base_dir = resolve_context_directory(working_directory, config["context_directory"])
    return base_dir / safe_name(ctx.run_id, "run")


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


def _prefix_lines(injection: PreparedInjection) -> list[str]:
    prefix_lines = []
    if injection.context_json_path:
        prefix_lines.extend(
            [
                "LangBot prepared read-only run context for this event.",
                f"- Context JSON: {injection.context_json_path}",
                f"- Context Markdown: {injection.context_markdown_path}",
            ]
        )
    if prefix_lines:
        prefix_lines.append("Use these files only as scoped context for the current LangBot event.")
    return prefix_lines


def prepare_injection(
    ctx: AgentRunContext,
    input_text: str,
    working_directory: str,
    config: dict[str, typing.Any],
) -> PreparedInjection:
    run_dir = run_context_directory(working_directory, ctx, config)
    injection = PreparedInjection()

    injection.context_json_path, injection.context_markdown_path = write_context_files(
        ctx,
        input_text,
        working_directory,
        run_dir,
    )

    prefix_lines = _prefix_lines(injection)
    if prefix_lines:
        injection.prompt_prefix = "\n".join(prefix_lines)

    return injection


def prepare_remote_injection(
    ctx: AgentRunContext,
    input_text: str,
    workspace_key: str,
    config: dict[str, typing.Any],
) -> PreparedInjection:
    context_directory = safe_relative_posix_path(config["context_directory"])
    if not context_directory:
        raise ValueError("context directory must be a relative path for remote execution")

    run_dir = pathlib.PurePosixPath(context_directory) / safe_name(ctx.run_id, "run")
    injection = PreparedInjection()
    files: list[dict[str, typing.Any]] = []

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
