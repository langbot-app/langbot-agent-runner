"""Claude Code CLI command construction and subprocess execution."""

from __future__ import annotations

import asyncio
import shlex
import typing

from pkg import runner_utils


def build_command(
    config: dict[str, typing.Any],
    resume_session_id: str = "",
) -> list[str]:
    command = shlex.split(str(config["cli_command"]))
    if not command:
        command = ["claude"]

    command.append("-p")
    command.extend(["--output-format", "json"])
    if config["model"]:
        command.extend(["--model", str(config["model"])])
    if config.get("dangerously_skip_permissions"):
        command.append("--dangerously-skip-permissions")
    if resume_session_id:
        command.extend(["--resume", resume_session_id])

    return command


async def run_cli(
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
        env=runner_utils.inherited_harness_env(),
        **runner_utils.subprocess_kwargs(),
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin),
            timeout=timeout,
        )
    except TimeoutError:
        runner_utils.terminate_process(process)
        await process.wait()
        raise

    return (
        runner_utils.normalize_returncode(process),
        runner_utils.safe_output(stdout.decode("utf-8", errors="replace")),
        runner_utils.safe_output(stderr.decode("utf-8", errors="replace")),
    )
