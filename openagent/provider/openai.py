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


class OpenAIConverterMixin(MessageConverterMixin):
    """Converts between canonical message format and OpenAI's chat format."""

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
                            converted.append({
                                "role": "tool",
                                "tool_call_id": block.tool_use_id,
                                "content": block.content,
                            })

        return {"messages": converted}

    def convert_response(self, response: Any) -> Message:
        choice = response.choices[0]
        message = choice.message
        blocks: list[ContentBlock] = []

        if message.content:
            blocks.append(TextBlock(text=message.content))

        if message.tool_calls:
            for tc in message.tool_calls:
                blocks.append(ToolUseBlock(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
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
            api_key=effective_api_key,
            base_url=base_url or None,
            **kwargs
        )

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
    ) -> AsyncIterator[str]:
        """Stream text chunks from OpenAI."""
        converted = self.convert_messages(messages, system_prompt)
        api_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": converted["messages"],
            "stream": True,
            **kwargs,
        }
        if tools:
            api_kwargs["tools"] = self.convert_tools(tools)

        stream = await self._client.chat.completions.create(**api_kwargs)
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
