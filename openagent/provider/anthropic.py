from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from openagent.core.retry import get_provider_retryable_exceptions, with_retry
from openagent.core.types import (
    ContentBlock,
    Message,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
)
from openagent.provider.base import BaseProvider
from openagent.provider.converter import MessageConverterMixin


class AnthropicConverterMixin(MessageConverterMixin):
    """Converts between canonical message format and Anthropic's Messages API format."""

    def convert_messages(
        self, messages: list[Message], system_prompt: str = ""
    ) -> dict[str, Any]:
        converted: list[dict[str, Any]] = []
        result: dict[str, Any] = {}

        # Anthropic takes system as a top-level parameter, not in messages
        system_parts: list[str] = []
        if system_prompt:
            system_parts.append(system_prompt)

        for msg in messages:
            if msg.role == "system":
                system_parts.append(msg.text)

            elif msg.role == "user":
                converted.append({
                    "role": "user",
                    "content": msg.text if isinstance(msg.content, str) else self._convert_content_blocks(msg.content),
                })

            elif msg.role == "assistant":
                if isinstance(msg.content, str):
                    converted.append({"role": "assistant", "content": msg.content})
                else:
                    blocks = self._convert_content_blocks(msg.content)
                    converted.append({"role": "assistant", "content": blocks})

            elif msg.role == "tool_result":
                # Anthropic expects tool results in a user message
                if isinstance(msg.content, list):
                    blocks = [
                        {
                            "type": "tool_result",
                            "tool_use_id": b.tool_use_id,
                            "content": b.content,
                            **({"is_error": True} if b.is_error else {}),
                        }
                        for b in msg.content
                        if isinstance(b, ToolResultBlock)
                    ]
                    converted.append({"role": "user", "content": blocks})

        result["messages"] = converted
        if system_parts:
            result["system"] = "\n\n".join(system_parts)
        return result

    def _convert_content_blocks(self, blocks: list[ContentBlock]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for b in blocks:
            if isinstance(b, TextBlock):
                converted.append({"type": "text", "text": b.text})
            elif isinstance(b, ToolUseBlock):
                converted.append({
                    "type": "tool_use",
                    "id": b.id,
                    "name": b.name,
                    "input": b.arguments,
                })
        return converted

    def convert_response(self, response: Any) -> Message:
        blocks: list[ContentBlock] = []
        for block in response.content:
            if block.type == "text":
                blocks.append(TextBlock(text=block.text))
            elif block.type == "tool_use":
                blocks.append(ToolUseBlock(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                ))

        if len(blocks) == 1 and isinstance(blocks[0], TextBlock):
            return Message(role="assistant", content=blocks[0].text)
        return Message(role="assistant", content=blocks)

    def convert_tools(self, tools: list[ToolDef]) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]


class AnthropicProvider(AnthropicConverterMixin, BaseProvider):
    def __init__(self, model: str = "claude-sonnet-4-20250514", api_key: str | None = None, max_retries: int = 3, **kwargs: Any) -> None:
        super().__init__(model=model, api_key=api_key, **kwargs)
        self._max_retries = max_retries
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            raise ImportError("Install anthropic: pip install anthropic")
        client_kwargs = {k: v for k, v in kwargs.items() if k != "api_key"}
        if api_key is not None:
            client_kwargs["api_key"] = api_key
        self._client = AsyncAnthropic(**client_kwargs)

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
        retryable = get_provider_retryable_exceptions("anthropic")

        @with_retry(max_retries=self._max_retries, retryable_exceptions=retryable)
        async def _call() -> Message:
            converted = self.convert_messages(messages, system_prompt)
            api_kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": converted["messages"],
                "max_tokens": kwargs.pop("max_tokens", 4096),
                **kwargs,
            }
            if "system" in converted:
                api_kwargs["system"] = converted["system"]
            if tools:
                api_kwargs["tools"] = self.convert_tools(tools)
            response = await self._client.messages.create(**api_kwargs)
            return self.convert_response(response)

        return await _call()

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream text chunks from Anthropic."""
        converted = self.convert_messages(messages, system_prompt)
        api_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": converted["messages"],
            "max_tokens": kwargs.pop("max_tokens", 4096),
            "stream": True,
            **kwargs,
        }
        if "system" in converted:
            api_kwargs["system"] = converted["system"]
        if tools:
            api_kwargs["tools"] = self.convert_tools(tools)

        async with self._client.messages.stream(**api_kwargs) as response:
            async for text in response.text_stream:
                yield text


#
# python .\example.py anthropic
