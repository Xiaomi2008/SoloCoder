from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from openagent.core.retry import get_provider_retryable_exceptions, with_retry
from openagent.core.types import (
    ContentBlock,
    ImageBlock,
    Message,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
)
from openagent.provider.base import BaseProvider
from openagent.provider.converter import MessageConverterMixin
from openagent.providers import (
    ProviderError,
    ProviderMessageCompleted,
    ProviderMessageStarted,
    ProviderReasoningDelta,
    ProviderStreamEvent,
    ProviderTextDelta,
    ProviderToolCall,
)


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
            elif isinstance(b, ImageBlock):
                converted.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": b.mime_type, "data": b.data},
                })
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


def mark_cache_breakpoints(api_kwargs: dict[str, Any]) -> None:
    """Ask Anthropic to cache the request's prefix: tools, system, history.

    Anthropic caches only up to a ``cache_control`` marker; without one every
    turn of an agent run paid for the whole history again. Three of the four
    allowed markers: the last tool schema, the system prompt, and the last
    block of the last message. The next turn's request starts with this one's
    exact content, so it reads all of it from cache — and so does a
    compaction's summary request, which is this conversation plus one message.
    """
    ephemeral = {"type": "ephemeral"}
    tools = api_kwargs.get("tools")
    if tools:
        tools[-1] = {**tools[-1], "cache_control": ephemeral}
    system = api_kwargs.get("system")
    if isinstance(system, str) and system:
        api_kwargs["system"] = [{"type": "text", "text": system, "cache_control": ephemeral}]
    messages = api_kwargs.get("messages")
    if messages:
        last = messages[-1]
        content = last.get("content")
        if isinstance(content, str) and content:
            content = [{"type": "text", "text": content}]
        if isinstance(content, list) and content:
            block = content[-1]
            # An empty text block cannot carry a marker.
            if block.get("type") != "text" or block.get("text"):
                content = [*content[:-1], {**block, "cache_control": ephemeral}]
                messages[-1] = {**last, "content": content}


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

        # The Messages API has no `reasoning_effort` and rejects the request;
        # extended thinking would need its signed blocks kept in the history.
        kwargs.pop("reasoning_effort", None)

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
            mark_cache_breakpoints(api_kwargs)
            response = await self._client.messages.create(**api_kwargs)
            return self.convert_response(response)

        return await _call()

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> AsyncIterator[ProviderStreamEvent]:
        """Stream a reply as the runtime's provider events.

        This yielded the reply's text as bare strings, which the runner does
        not recognise: every Claude turn in the app arrived empty, with no
        text and no tool calls, and was retried as an empty response. Tool
        calls arrive as a ``tool_use`` block whose input streams as JSON
        fragments; they are assembled here and emitted once, complete.
        """
        kwargs.pop("reasoning_effort", None)
        converted = self.convert_messages(messages, system_prompt)
        api_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": converted["messages"],
            "max_tokens": kwargs.pop("max_tokens", 8192),
            **kwargs,
        }
        if "system" in converted:
            api_kwargs["system"] = converted["system"]
        if tools:
            api_kwargs["tools"] = self.convert_tools(tools)
        mark_cache_breakpoints(api_kwargs)

        message_id = f"msg_{uuid.uuid4().hex[:24]}"
        # Tool-use blocks being assembled, by content-block index.
        pending: dict[int, dict[str, Any]] = {}
        async with self._client.messages.stream(**api_kwargs) as response:
            async for event in response:
                kind = getattr(event, "type", None)
                if kind == "message_start":
                    message_id = getattr(event.message, "id", None) or message_id
                    yield ProviderMessageStarted(message_id=message_id)
                elif kind == "content_block_start":
                    block = event.content_block
                    if getattr(block, "type", None) == "tool_use":
                        pending[event.index] = {"id": block.id, "name": block.name, "json": []}
                elif kind == "content_block_delta":
                    delta = event.delta
                    delta_kind = getattr(delta, "type", None)
                    if delta_kind == "text_delta" and delta.text:
                        yield ProviderTextDelta(message_id=message_id, delta=delta.text)
                    elif delta_kind == "thinking_delta" and delta.thinking:
                        yield ProviderReasoningDelta(message_id=message_id, delta=delta.thinking)
                    elif delta_kind == "input_json_delta" and event.index in pending:
                        pending[event.index]["json"].append(delta.partial_json)
                elif kind == "content_block_stop" and event.index in pending:
                    call = pending.pop(event.index)
                    raw = "".join(call["json"]).strip()
                    try:
                        arguments = json.loads(raw) if raw else {}
                    except ValueError:
                        yield ProviderError(
                            message_id=message_id,
                            error=f"{call['name']}: the tool input was not valid JSON: {raw[:200]}",
                        )
                        return
                    yield ProviderToolCall(
                        message_id=message_id, id=call["id"], name=call["name"], arguments=arguments
                    )
                elif kind == "message_stop":
                    yield ProviderMessageCompleted(message_id=message_id)
                # The SDK also yields its own "text" / "input_json" helper
                # events; they repeat the deltas above and are skipped.


#
# python .\example.py anthropic
