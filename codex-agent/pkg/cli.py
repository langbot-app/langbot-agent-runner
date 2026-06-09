"""Codex CLI command construction and subprocess execution."""

from __future__ import annotations

import asyncio
import shlex
import typing

from pkg import runner_utils


def build_command(
    config: dict[str, typing.Any],
    resume_session_id: str = "",
    output_last_message_path: str = "",
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
        command.extend(
            [
                "--config",
                f"approval_policy={runner_utils.toml_literal(str(config['approval_policy']))}",
            ]
        )

    for item in config["config_overrides"]:
        command.extend(["--config", item])

    command.extend(config["extra_args"])
    if use_resume:
        command.extend([resume_session_id, "-"])
    else:
        command.append("-")
    return command


def build_subprocess_env(config: dict[str, typing.Any]) -> dict[str, str]:
    env = runner_utils.inherited_harness_env()
    if config.get("codex_home"):
        env["CODEX_HOME"] = str(config["codex_home"])

    extra_env = runner_utils.loads_json_config(config.get("environment_json"), "environment-json") or {}
    if not isinstance(extra_env, dict):
        raise ValueError("environment-json must be a JSON object")
    for key, value in extra_env.items():
        key_text = str(key).strip()
        if not key_text or value is None:
            continue
        if runner_utils.is_blocked_env_key(key_text):
            raise ValueError(f"environment-json cannot override protected environment variable: {key_text}")
        env[key_text] = str(value)
    return env


async def run_cli(
    command: list[str],
    stdin: bytes,
    timeout: float,
    working_directory: str,
    config: dict[str, typing.Any],
) -> tuple[int, str, str]:
    env = build_subprocess_env(config)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=working_directory,
        env=env,
        **runner_utils.subprocess_kwargs(),
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin),
            timeout=timeout,
        )
    except BaseException:
        if process.returncode is None:
            runner_utils.terminate_process(process)
            await process.wait()
        raise

    return (
        runner_utils.normalize_returncode(process),
        runner_utils.safe_output(stdout.decode("utf-8", errors="replace")),
        runner_utils.safe_output(stderr.decode("utf-8", errors="replace")),
    )
