"""Claude Code stdout parsers."""

from __future__ import annotations

import json
import typing


def parse_stdout(stdout: str, output_format: str) -> tuple[str, str, dict[str, typing.Any]]:
    if output_format == "stream-json":
        return parse_stream_json(stdout)
    if output_format == "json":
        return parse_json_result(stdout)
    return stdout.strip(), "", {"output_format": output_format}


def parse_json_result(stdout: str) -> tuple[str, str, dict[str, typing.Any]]:
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


def parse_stream_json(stdout: str) -> tuple[str, str, dict[str, typing.Any]]:
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
