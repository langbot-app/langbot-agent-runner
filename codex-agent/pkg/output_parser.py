"""Codex stdout parsers."""

from __future__ import annotations

import json
import pathlib
import typing


def parse_stdout(
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
        content, session_id, metadata = parse_jsonl_events(stdout)
        return file_content or content, session_id, metadata
    return file_content or stdout.strip(), "", {"output_format": output_format}


def parse_jsonl_events(stdout: str) -> tuple[str, str, dict[str, typing.Any]]:
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
