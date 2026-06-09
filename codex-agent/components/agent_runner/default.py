"""Codex Agent default runner implementation."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import typing

from langbot_plugin.api.definition.components.agent_runner.runner import AgentRunner
from langbot_plugin.api.entities.builtin.agent_runner import (
    AgentRunContext,
    AgentRunnerCapabilities,
    AgentRunResult,
    AgentRunResultType,
)
from langbot_plugin.api.entities.builtin.provider.message import Message
from pkg import cli, output_parser, remote_client, runner_utils
from pkg import injection as context_injection

logger = logging.getLogger(__name__)

json = _json
_post_remote_run = remote_client.post_remote_run


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
            "execution_mode": config.get("execution-mode", "local") or "local",
            "remote_endpoint": config.get("remote-endpoint", ""),
            "remote_token": config.get("remote-token", ""),
            "remote_runtime_id": config.get("remote-runtime-id", "default") or "default",
            "remote_workspace_key": config.get("remote-workspace-key", ""),
            "remote_request_timeout": float(config.get("remote-request-timeout", 0) or 0),
            "extra_args": runner_utils.parse_args(config.get("extra-args", "")),
            "working_directory": config.get("working-directory", ""),
            "inject_context": runner_utils.to_bool(config.get("inject-context", True)),
            "context_directory": config.get(
                "context-directory",
                runner_utils.DEFAULT_CONTEXT_DIRECTORY,
            )
            or runner_utils.DEFAULT_CONTEXT_DIRECTORY,
            "model": config.get("model", ""),
            "approval_policy": config.get("approval-policy", "never") or "",
            "sandbox": config.get("sandbox", "read-only") or "",
            "output_format": config.get("output-format", "json") or "json",
            "skip_git_repo_check": runner_utils.to_bool(config.get("skip-git-repo-check", True)),
            "ephemeral": runner_utils.to_bool(config.get("ephemeral")),
            "ignore_rules": runner_utils.to_bool(config.get("ignore-rules")),
            "config_overrides": runner_utils.filter_codex_config_overrides(
                runner_utils.parse_config_overrides(config.get("config-overrides", ""))
            ),
            "environment_json": config.get("environment-json", ""),
            "resume": runner_utils.to_bool(config.get("resume", True)),
            "timeout": float(config.get("timeout", 300) or 300),
        }

    def _get_resume_session_id(self, ctx: AgentRunContext) -> str:
        return str(ctx.state.conversation.get("external.session_id") or "")

    def _get_working_directory(self, ctx: AgentRunContext, config: dict[str, typing.Any]) -> str:
        configured = str(config["working_directory"] or "").strip()
        if configured:
            return runner_utils.normalize_working_directory(configured)

        stored = str(ctx.state.conversation.get("external.working_directory") or "").strip()
        if stored:
            return runner_utils.normalize_working_directory(stored)

        return runner_utils.normalize_working_directory(os.getcwd())

    def _get_input_text(self, ctx: AgentRunContext) -> str:
        return ctx.input.to_text()

    def _build_stdin(
        self,
        input_text: str,
        injection: context_injection.PreparedInjection | None = None,
    ) -> bytes:
        if injection and injection.prompt_prefix:
            input_text = f"{injection.prompt_prefix}\n\nUser event input:\n{input_text}"
        return input_text.encode("utf-8")

    def _create_langbot_mcp_bridge(self, ctx: AgentRunContext) -> typing.Any | None:
        try:
            return self.create_external_mcp_bridge(ctx)
        except RuntimeError as e:
            if "runtime is not bound" in str(e):
                return None
            raise
        except AttributeError as e:
            if "_plugin_runtime_handler" in str(e):
                return None
            raise

    async def _run_cli(
        self,
        command: list[str],
        stdin: bytes,
        timeout: float,
        working_directory: str,
        config: dict[str, typing.Any],
    ) -> tuple[int, str, str]:
        return await cli.run_cli(command, stdin, timeout, working_directory, config)

    async def _run_remote(
        self,
        request_payload: dict[str, typing.Any],
        config: dict[str, typing.Any],
    ) -> dict[str, typing.Any]:
        request_timeout = config["remote_request_timeout"] or (config["timeout"] + 30)
        return await asyncio.to_thread(
            _post_remote_run,
            str(config["remote_endpoint"]),
            str(config["remote_token"] or ""),
            request_payload,
            float(request_timeout),
        )

    def _run_completed(
        self,
        ctx: AgentRunContext,
        session_id: str,
        working_directory: str,
        metadata: dict[str, typing.Any],
        *,
        runtime_id: str | None = None,
        workspace_key: str | None = None,
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
        if runtime_id:
            data["external"]["runtime_id"] = runtime_id
        if workspace_key:
            data["external"]["workspace_key"] = workspace_key
        return AgentRunResult(
            run_id=ctx.run_id,
            type=AgentRunResultType.RUN_COMPLETED,
            data=data,
        )

    async def run(self, ctx: AgentRunContext) -> typing.AsyncGenerator[AgentRunResult, None]:
        """Run Codex CLI and return the final assistant message."""
        config = self._get_config(ctx)
        input_text = self._get_input_text(ctx)

        resume_session_id = self._get_resume_session_id(ctx)
        if str(config["execution_mode"]).strip().lower() == "remote":
            async for result in self._run_remote_mode(ctx, input_text, resume_session_id, config):
                yield result
            return

        working_directory = self._get_working_directory(ctx, config)

        if not os.path.isdir(working_directory):
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"Codex working directory not found: {working_directory}",
                code="codex.working_directory_not_found",
            )
            return

        bridge = None
        try:
            run_dir = context_injection.run_context_directory(working_directory, ctx, config)
            langbot_mcp_server = None
            bridge = self._create_langbot_mcp_bridge(ctx)
            if bridge is not None:
                bridge.start()
                langbot_mcp_server = bridge.mcp_server_config()
            injection = context_injection.prepare_injection(
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
        codex_home = run_dir / "codex-home"
        managed_mcp_config = (
            context_injection.render_codex_mcp_servers_config(injection.mcp_config_data)
            if injection.mcp_config_data
            else ""
        )
        runner_utils.prepare_codex_home(codex_home, managed_config=managed_mcp_config)
        config["codex_home"] = str(codex_home)
        output_last_message_path = str(run_dir / "codex-last-message.txt")
        command = cli.build_command(
            config,
            resume_session_id,
            output_last_message_path,
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
        except TimeoutError:
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

        (run_dir / "codex-events.jsonl").write_text(runner_utils.safe_output(stdout), encoding="utf-8")
        if stderr:
            (run_dir / "codex-stderr.log").write_text(runner_utils.safe_output(stderr), encoding="utf-8")

        if returncode != 0:
            error = stderr.strip() or stdout.strip() or f"Codex CLI exited with code {returncode}"
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=error,
                code="codex.cli_error",
            )
            return

        content, session_id, metadata = output_parser.parse_stdout(
            stdout,
            config["output_format"],
            output_last_message_path,
        )
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

    async def _run_remote_mode(
        self,
        ctx: AgentRunContext,
        input_text: str,
        resume_session_id: str,
        config: dict[str, typing.Any],
    ) -> typing.AsyncGenerator[AgentRunResult, None]:
        endpoint = str(config["remote_endpoint"] or "").strip()
        if not endpoint:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error="remote-endpoint is required when execution-mode=remote",
                code="codex.remote_config_error",
            )
            return

        runtime_id = str(config["remote_runtime_id"] or "default")
        workspace_key = remote_client.remote_workspace_key(ctx, config)

        try:
            injection = context_injection.prepare_remote_injection(
                ctx,
                input_text,
                workspace_key,
                config,
            )
            stdin = self._build_stdin(input_text, injection)
        except Exception as e:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"Codex remote request preparation failed: {e}",
                code="codex.remote_preparation_error",
            )
            return

        command_config = {
            key: value
            for key, value in config.items()
            if key
            not in {
                "execution_mode",
                "remote_endpoint",
                "remote_token",
                "remote_runtime_id",
                "remote_workspace_key",
                "remote_request_timeout",
                "working_directory",
                "context_directory",
                "inject_context",
                "environment_json",
            }
        }

        request_payload = {
            "schema": "langbot.codex.remote_run.v1",
            "run_id": ctx.run_id,
            "runtime_id": runtime_id,
            "workspace_key": workspace_key,
            "resume_session_id": resume_session_id,
            "stdin": stdin.decode("utf-8", errors="replace"),
            "timeout": config["timeout"],
            "config": command_config,
            "files": injection.files,
        }

        response = await self._run_remote(request_payload, config)
        if not response.get("ok"):
            code = str(response.get("code") or "remote_error")
            error = str(response.get("error") or "Codex remote runner error")
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=error,
                code=f"codex.{code}",
                retryable=bool(response.get("retryable")),
            )
            return

        returncode = int(response.get("returncode") or 0)
        stdout = str(response.get("stdout") or "")
        stderr = str(response.get("stderr") or "")
        remote_working_directory = str(response.get("working_directory") or workspace_key)

        if returncode != 0:
            error = stderr.strip() or stdout.strip() or f"Codex CLI exited with code {returncode}"
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=error,
                code="codex.cli_error",
            )
            return

        content, session_id, metadata = output_parser.parse_stdout(stdout, config["output_format"])
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
            "external.runtime_id",
            runtime_id,
            scope="conversation",
        )
        yield AgentRunResult.state_updated(
            ctx.run_id,
            "external.workspace_key",
            workspace_key,
            scope="conversation",
        )
        yield self._run_completed(
            ctx,
            session_id,
            remote_working_directory,
            metadata,
            runtime_id=runtime_id,
            workspace_key=workspace_key,
        )
