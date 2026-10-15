from __future__ import annotations

import json
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


class OllamaConverterMixin(MessageConverterMixin):
    """Converts between canonical message format and Ollama's chat format."""

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
                    text_parts = [b.text for b in msg.content if isinstance(b, TextBlock)]
                    entry["content"] = "\n".join(text_parts) if text_parts else ""
                    tool_calls = [
                        {
                            "function": {
                                "name": b.name,
                                "arguments": b.arguments,
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
                            converted.append({
                                "role": "tool",
                                "content": block.content,
                            })

        return {"messages": converted}

    def convert_response(self, response: Any) -> Message:
        message = response.message
        blocks: list[ContentBlock] = []

        if message.content:
            blocks.append(TextBlock(text=message.content))

        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                arguments = tc.function.arguments
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                blocks.append(ToolUseBlock(
                    name=tc.function.name,
                    arguments=arguments,
                ))

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


class OllamaProvider(OllamaConverterMixin, BaseProvider):
    def __init__(
        self,
        model: str = "llama3",
        api_key: str | None = None,
        host: str | None = None,
        max_retries: int = 3,
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model, api_key=api_key, **kwargs)
        self._max_retries = max_retries
        try:
            from ollama import AsyncClient
        except ImportError:
            raise ImportError("Install ollama: pip install ollama")
        client_kwargs: dict[str, Any] = {**kwargs}
        if host is not None:
            client_kwargs["host"] = host
        self._client = AsyncClient(**client_kwargs)

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
        retryable = get_provider_retryable_exceptions("ollama")

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
            response = await self._client.chat(**api_kwargs)
            return self.convert_response(response)

        return await _call()

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream text chunks from Ollama."""
        converted = self.convert_messages(messages, system_prompt)
        api_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": converted["messages"],
            "stream": True,
            **kwargs,
        }
        if tools:
            api_kwargs["tools"] = self.convert_tools(tools)

        stream = await self._client.chat(**api_kwargs)
        async for chunk in stream:
            if hasattr(chunk, "message") and chunk.message.content:
                yield chunk.message.content
