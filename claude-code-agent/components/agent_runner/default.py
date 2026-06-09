"""Claude Code Agent default runner implementation."""

from __future__ import annotations

import asyncio
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
        return {
            "cli_command": "claude",
            "execution_mode": config.get("execution-mode", "local") or "local",
            "remote_endpoint": config.get("remote-endpoint", ""),
            "remote_token": config.get("remote-token", ""),
            "remote_runtime_id": "default",
            "working_directory": config.get("working-directory", ""),
            "context_directory": config.get("context-directory", runner_utils.DEFAULT_CONTEXT_DIRECTORY)
            or runner_utils.DEFAULT_CONTEXT_DIRECTORY,
            "model": config.get("model", ""),
            "output_format": "json",
            "dangerously_skip_permissions": runner_utils.to_bool(config.get("dangerously-skip-permissions")),
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
        prepared_injection: context_injection.PreparedInjection | None = None,
    ) -> bytes:
        if prepared_injection and prepared_injection.prompt_prefix:
            input_text = f"{prepared_injection.prompt_prefix}\n\nUser event input:\n{input_text}"
        return input_text.encode("utf-8")

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
            env=runner_utils.inherited_harness_env(),
            **runner_utils.subprocess_kwargs(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(stdin), timeout=timeout)
        except TimeoutError:
            runner_utils.terminate_process(process)
            await process.wait()
            raise
        return (
            runner_utils.normalize_returncode(process),
            runner_utils.safe_output(stdout.decode("utf-8", errors="replace")),
            runner_utils.safe_output(stderr.decode("utf-8", errors="replace")),
        )

    async def _run_remote(
        self,
        request_payload: dict[str, typing.Any],
        config: dict[str, typing.Any],
        mcp_handler: remote_client.channel.MCPHandler | None = None,
    ) -> dict[str, typing.Any]:
        return await remote_client.run_remote_channel(
            str(config["remote_endpoint"]),
            str(config["remote_token"] or ""),
            request_payload,
            float(config["timeout"] + 30),
            mcp_handler=mcp_handler,
        )

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
                "provider": "claude_code",
                "session_id": session_id or None,
                "working_directory": working_directory,
            },
            "claude_code": {k: v for k, v in metadata.items() if v is not None},
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
        """Run Claude Code CLI and return stdout as a final assistant message."""
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
                error=f"Claude Code working directory not found: {working_directory}",
                code="claude_code.working_directory_not_found",
            )
            return

        try:
            prepared_injection = context_injection.prepare_injection(
                ctx,
                input_text,
                working_directory,
                config,
            )
        except Exception as e:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"Claude Code context injection failed: {e}",
                code="claude_code.context_injection_error",
            )
            return

        command = cli.build_command(config, resume_session_id)
        stdin = self._build_stdin(input_text, prepared_injection)

        try:
            returncode, stdout, stderr = await self._run_cli(command, stdin, config["timeout"], working_directory)
        except FileNotFoundError:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"Claude Code CLI command not found: {command[0]}",
                code="claude_code.command_not_found",
            )
            return
        except TimeoutError:
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
        if returncode != 0:
            error = stderr.strip() or stdout.strip() or f"Claude Code CLI exited with code {returncode}"
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=error,
                code="claude_code.cli_error",
            )
            return

        content, session_id, metadata = output_parser.parse_stdout(stdout, config["output_format"])
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
                code="claude_code.remote_config_error",
            )
            return

        runtime_id = str(config["remote_runtime_id"] or "default")
        workspace_key = remote_client.remote_workspace_key(ctx)

        try:
            prepared_injection = context_injection.prepare_remote_injection(
                ctx,
                input_text,
                workspace_key,
                config,
            )
            stdin = self._build_stdin(input_text, prepared_injection)
        except Exception as e:
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=f"Claude Code remote request preparation failed: {e}",
                code="claude_code.remote_preparation_error",
            )
            return

        command_config = {
            "cli_command": config["cli_command"],
            "model": config["model"],
            "output_format": config["output_format"],
            "dangerously_skip_permissions": config["dangerously_skip_permissions"],
        }

        request_payload = {
            "schema": "langbot.claude_code.remote_run.v1",
            "agent": "claude_code",
            "run_id": ctx.run_id,
            "runtime_id": runtime_id,
            "workspace_key": workspace_key,
            "resume_session_id": resume_session_id,
            "stdin": stdin.decode("utf-8", errors="replace"),
            "timeout": config["timeout"],
            "config": command_config,
            "files": prepared_injection.files,
        }

        bridge = self._create_langbot_mcp_bridge(ctx)
        mcp_handler = bridge.handle_mcp_method if bridge is not None else None

        response = await self._run_remote(request_payload, config, mcp_handler)
        if not response.get("ok"):
            code = str(response.get("code") or "remote_error")
            error = str(response.get("error") or "Claude Code remote runner error")
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=error,
                code=f"claude_code.{code}",
                retryable=bool(response.get("retryable")),
            )
            return

        returncode = int(response.get("returncode") or 0)
        stdout = str(response.get("stdout") or "")
        stderr = str(response.get("stderr") or "")
        remote_working_directory = str(response.get("working_directory") or workspace_key)

        if returncode != 0:
            error = stderr.strip() or stdout.strip() or f"Claude Code CLI exited with code {returncode}"
            yield AgentRunResult.run_failed(
                ctx.run_id,
                error=error,
                code="claude_code.cli_error",
            )
            return

        content, session_id, metadata = output_parser.parse_stdout(stdout, config["output_format"])
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
