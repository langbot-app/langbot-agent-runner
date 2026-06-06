"""DashScope API client for AgentRunner.

This module provides a minimal DashScope API client using the official dashscope SDK.
"""

from __future__ import annotations

import logging
import re
import typing

from dashscope import Application

logger = logging.getLogger(__name__)


class DashScopeAPIError(Exception):
    """DashScope API error."""

    def __init__(self, message: str, code: str = "dashscope.api_error"):
        self.message = message
        self.code = code
        super().__init__(message)


class DashScopeConfigError(Exception):
    """DashScope configuration error."""

    def __init__(self, message: str, code: str = "dashscope.config_invalid"):
        self.message = message
        self.code = code
        super().__init__(message)


def replace_references(text: str, references_dict: dict[str, str], references_quote: str) -> str:
    """Replace reference tags with readable reference text.

    Args:
        text: Text containing <ref>[index_id]</ref> tags
        references_dict: Mapping from index_id to doc_name
        references_quote: Prefix for reference text (e.g., "参考资料来自:")

    Returns:
        Text with references replaced
    """
    pattern = re.compile(r"<ref>\[(.*?)\]</ref>")

    def replacement(match):
        ref_key = match.group(1)
        if ref_key in references_dict:
            return f"({references_quote} {references_dict[ref_key]})"
        else:
            return match.group(0)

    return pattern.sub(replacement, text)


def extract_references_from_chunk(
    stream_output: dict[str, typing.Any],
) -> dict[str, str]:
    """Extract document references from DashScope response chunk.

    Args:
        stream_output: The output section of a DashScope response chunk

    Returns:
        Dictionary mapping index_id to doc_name
    """
    references_dict: dict[str, str] = {}
    references_list = stream_output.get("doc_references", [])

    if references_list:
        for doc in references_list:
            index_id = doc.get("index_id")
            doc_name = doc.get("doc_name")
            if index_id is not None and doc_name is not None:
                references_dict[index_id] = doc_name

    return references_dict


class DashScopeClient:
    """Minimal DashScope API client for AgentRunner.

    Supports:
    - Agent mode with thinking/reasoning
    - Workflow mode with message format streaming
    """

    def __init__(
        self,
        api_key: str,
        app_id: str,
        app_type: str = "agent",
        references_quote: str = "参考资料来自:",
    ):
        self.api_key = api_key
        self.app_id = app_id
        self.app_type = app_type
        self.references_quote = references_quote

    def call_agent(
        self,
        prompt: str,
        session_id: str = "",
        enable_thinking: bool = True,
    ) -> typing.Any:
        """Call DashScope agent application.

        Args:
            prompt: User input text
            session_id: Session ID for multi-turn conversation
            enable_thinking: Whether to enable thinking/reasoning

        Yields:
            Response chunks from DashScope API
        """
        response = Application.call(
            api_key=self.api_key,
            app_id=self.app_id,
            prompt=prompt,
            stream=True,
            incremental_output=True,
            session_id=session_id,
            enable_thinking=enable_thinking,
            has_thoughts=enable_thinking,
        )

        yield from response

    def call_workflow(
        self,
        prompt: str,
        session_id: str = "",
        biz_params: dict[str, typing.Any] | None = None,
    ) -> typing.Any:
        """Call DashScope workflow application.

        Args:
            prompt: User input text
            session_id: Session ID for multi-turn conversation
            biz_params: Business parameters for workflow

        Yields:
            Response chunks from DashScope API
        """
        biz_params = biz_params or {}

        response = Application.call(
            api_key=self.api_key,
            app_id=self.app_id,
            prompt=prompt,
            stream=True,
            incremental_output=True,
            session_id=session_id,
            biz_params=biz_params,
            flow_stream_mode="message_format",
        )

        yield from response
