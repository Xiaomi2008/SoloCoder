from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from openagent.provider.base import BaseProvider
from openagent.provider.converter import MessageConverterMixin
from openagent.providers import (
    ProviderError,
    ProviderMessageCompleted,
    ProviderMessageStarted,
    ProviderStreamEvent,
    ProviderTextDelta,
    ProviderToolCall,
)
from openagent.core.retry import get_provider_retryable_exceptions, with_retry
from openagent.core.types import (
    ContentBlock,
    Message,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
)


logger = logging.getLogger("openagent.provider.openai")


class OpenAIConverterMixin(MessageConverterMixin):
    """Converts between canonical message format and OpenAI's chat format."""

    def _log_response_issue(self, issue: str, response: Any, **details: Any) -> None:
        """Hook for provider-specific response anomaly logging."""

    @staticmethod
    def _safe_response_payload(response: Any) -> str:
        try:
            if hasattr(response, "model_dump"):
                payload = response.model_dump(exclude_none=True)
            elif hasattr(response, "dict"):
                payload = response.dict()
            else:
                payload = repr(response)

            text = (
                payload
                if isinstance(payload, str)
                else json.dumps(payload, default=str)
            )
        except Exception:
            text = repr(response)

        if len(text) > 4000:
            return text[:4000] + "... [truncated]"
        return text

    def convert_messages(
        self, messages: list[Message], system_prompt: str = ""
    ) -> dict[str, Any]:
        converted: list[dict[str, Any]] = []

        if system_prompt:
            converted.append({"role": "system", "content": system_prompt})

        for msg in messages:
            if msg.role == "system":
                converted.append({"role": "system", "content": msg.text})

            elif msg.role == "user":
                converted.append({"role": "user", "content": msg.text})

            elif msg.role == "assistant":
                entry: dict[str, Any] = {"role": "assistant"}
                if isinstance(msg.content, str):
                    entry["content"] = msg.content
                else:
                    text_parts = [
                        b.text for b in msg.content if isinstance(b, TextBlock)
                    ]
                    entry["content"] = "\n".join(text_parts) if text_parts else None
                    tool_calls = [
                        {
                            "id": b.id,
                            "type": "function",
                            "function": {
                                "name": b.name,
                                "arguments": json.dumps(b.arguments),
                            },
                        }
                        for b in msg.content
                        if isinstance(b, ToolUseBlock)
                    ]
                    if tool_calls:
                        entry["tool_calls"] = tool_calls
                converted.append(entry)

            elif msg.role == "tool_result":
                if isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, ToolResultBlock):
                            converted.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": block.tool_use_id,
                                    "content": block.content,
                                }
                            )

        return {"messages": converted}

    def convert_response(self, response: Any) -> Message:
        choice = response.choices[0]
        message = choice.message
        blocks: list[ContentBlock] = []

        if message.content:
            blocks.append(TextBlock(text=message.content))

        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    arguments = json.loads(tc.function.arguments)
                except Exception as exc:
                    self._log_response_issue(
                        "invalid_tool_arguments",
                        response,
                        tool_name=getattr(tc.function, "name", None),
                        arguments=getattr(tc.function, "arguments", None),
                        error=str(exc),
                    )
                    raise

                blocks.append(
                    ToolUseBlock(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=arguments,
                    )
                )

        if not blocks:
            self._log_response_issue(
                "empty_assistant_message",
                response,
                finish_reason=getattr(choice, "finish_reason", None),
            )

        if len(blocks) == 1 and isinstance(blocks[0], TextBlock):
            return Message(role="assistant", content=blocks[0].text)
        return Message(role="assistant", content=blocks)

    def convert_tools(self, tools: list[ToolDef]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]


class OpenAIProvider(OpenAIConverterMixin, BaseProvider):
    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
        max_retries: int = 3,
        base_url: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model, api_key=api_key, **kwargs)
        self._max_retries = max_retries
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("Install openai: pip install openai")

        # For local/compatible APIs (custom base_url), use empty string for API key if not provided
        # This allows servers like Ollama, LM Studio to work without authentication
        effective_api_key = api_key
        if base_url and not api_key:
            effective_api_key = ""  # Empty string tells OpenAI client no auth required

        self._client = AsyncOpenAI(
            api_key=effective_api_key, base_url=base_url or None, **kwargs
        )
        self.base_url = base_url

    def _log_response_issue(self, issue: str, response: Any, **details: Any) -> None:
        metadata = {
            "issue": issue,
            "model": self.model,
            "base_url": self.base_url,
            **details,
        }
        logger.warning("OpenAI-compatible response anomaly: %s", metadata)
        logger.debug("Raw response payload: %s", self._safe_response_payload(response))

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> Message:
        return await self._chat_with_retry(messages, tools, system_prompt, **kwargs)

    async def _chat_with_retry(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None,
        system_prompt: str,
        **kwargs: Any,
    ) -> Message:
        retryable = get_provider_retryable_exceptions("openai")

        @with_retry(max_retries=self._max_retries, retryable_exceptions=retryable)
        async def _call() -> Message:
            converted = self.convert_messages(messages, system_prompt)
            api_kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": converted["messages"],
                **kwargs,
            }
            if tools:
                api_kwargs["tools"] = self.convert_tools(tools)
            response = await self._client.chat.completions.create(**api_kwargs)
            return self.convert_response(response)

        return await _call()

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> AsyncIterator[ProviderStreamEvent]:
        """Stream provider events from OpenAI text responses."""
        converted = self.convert_messages(messages, system_prompt)
        api_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": converted["messages"],
            "stream": True,
            **kwargs,
        }
        if tools:
            api_kwargs["tools"] = self.convert_tools(tools)

        fallback_message_id = f"msg_{uuid.uuid4().hex[:24]}"
        message_id: str | None = None
        started = False
        tool_call_buffers: dict[int, dict[str, Any]] = {}

        try:
            stream = await self._client.chat.completions.create(**api_kwargs)
            async for chunk in stream:
                if message_id is None:
                    chunk_id = getattr(chunk, "id", None)
                    message_id = chunk_id or fallback_message_id

                if not started:
                    started = True
                    yield ProviderMessageStarted(message_id=message_id)

                if chunk.choices and chunk.choices[0].delta.content:
                    yield ProviderTextDelta(
                        message_id=message_id,
                        delta=chunk.choices[0].delta.content,
                    )

                if chunk.choices:
                    delta = chunk.choices[0].delta
                    for tool_call_delta in getattr(delta, "tool_calls", None) or []:
                        index = getattr(tool_call_delta, "index", 0)
                        buffer = tool_call_buffers.setdefault(
                            index,
                            {
                                "id": "",
                                "name": "",
                                "arguments": "",
                                "emitted": False,
                            },
                        )

                        tool_call_id = getattr(tool_call_delta, "id", None)
                        if tool_call_id:
                            buffer["id"] = tool_call_id

                        function = getattr(tool_call_delta, "function", None)
                        function_name = getattr(function, "name", None)
                        if function_name:
                            buffer["name"] += function_name

                        function_arguments = getattr(function, "arguments", None)
                        if function_arguments:
                            buffer["arguments"] += function_arguments

                        if (
                            not buffer["emitted"]
                            and buffer["id"]
                            and buffer["name"]
                            and buffer["arguments"]
                        ):
                            try:
                                arguments = json.loads(buffer["arguments"])
                            except json.JSONDecodeError:
                                continue

                            buffer["emitted"] = True
                            yield ProviderToolCall(
                                message_id=message_id,
                                id=buffer["id"],
                                name=buffer["name"],
                                arguments=arguments,
                            )
        except Exception as exc:
            message_id = message_id or fallback_message_id
            if not started:
                started = True
                yield ProviderMessageStarted(message_id=message_id)
            yield ProviderError(message_id=message_id, error=str(exc))
            return

        if message_id is None:
            message_id = fallback_message_id
            yield ProviderMessageStarted(message_id=message_id)

        yield ProviderMessageCompleted(message_id=message_id)
