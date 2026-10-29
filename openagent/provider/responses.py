"""Adapter for OpenAI's **Responses** API (``/v1/responses``).

A separate adapter from the chat-completions one because the wire format is
genuinely different: items instead of messages, ``instructions`` instead of a
system message, ``function_call`` / ``function_call_output`` items instead of
assistant ``tool_calls`` + ``role=tool`` messages.

This is also the only dialect the ChatGPT Plus/Pro subscription backend
accepts — ``api.openai.com/v1/chat/completions`` rejects subscription OAuth
tokens with 401, so subscription access goes through
``chatgpt.com/backend-api/codex/responses``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from openagent.core.logging import logger
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
from openagent.providers.events import (
    ProviderError,
    ProviderMessageCompleted,
    ProviderMessageStarted,
    ProviderStreamEvent,
    ProviderTextDelta,
    ProviderToolCall,
)


class ResponsesConverterMixin(MessageConverterMixin):
    """Canonical messages <-> Responses API items."""

    def convert_messages(self, messages: list[Message], system_prompt: str = "") -> dict[str, Any]:
        instructions: list[str] = []
        if system_prompt:
            instructions.append(system_prompt)

        items: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "system":
                instructions.append(msg.text)
                continue

            if msg.role == "user":
                items.append({"role": "user", "content": self._user_content(msg)})
                continue

            if msg.role == "tool_result":
                for result in msg.tool_results:
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": result.tool_use_id,
                            "output": result.content if isinstance(result.content, str) else json.dumps(result.content),
                        }
                    )
                continue

            # assistant: emit function_call items, then the message item
            for call in msg.tool_calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    }
                )
            text = msg.text
            if text:
                items.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )

        payload: dict[str, Any] = {"input": items}
        if instructions:
            payload["instructions"] = "\n\n".join(instructions)
        return payload

    @staticmethod
    def _user_content(msg: Message) -> Any:
        if isinstance(msg.content, str) or not msg.has_images:
            return msg.text
        blocks: list[dict[str, Any]] = []
        for block in msg.content:
            if isinstance(block, TextBlock):
                blocks.append({"type": "input_text", "text": block.text})
            elif isinstance(block, ImageBlock):
                blocks.append({"type": "input_image", "image_url": block.url})
        return blocks

    def convert_tools(self, tools: list[ToolDef]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in tools
        ]

    def convert_response(self, response: Any) -> Message:
        blocks: list[ContentBlock] = []
        for item in getattr(response, "output", []) or []:
            item_type = getattr(item, "type", None)
            if item_type == "message":
                for part in getattr(item, "content", []) or []:
                    text = getattr(part, "text", None)
                    if text is None and isinstance(part, dict):
                        text = part.get("text")
                    if text:
                        blocks.append(TextBlock(text=text))
            elif item_type == "function_call":
                arguments = getattr(item, "arguments", None)
                try:
                    parsed = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
                except json.JSONDecodeError:
                    logger.warning(f"Responses API returned non-JSON tool arguments for {getattr(item, 'name', '?')}")
                    parsed = {"_raw": arguments}
                blocks.append(
                    ToolUseBlock(
                        id=getattr(item, "call_id", None) or f"call_{len(blocks)}",
                        name=getattr(item, "name", "") or "",
                        arguments=parsed,
                    )
                )
        if not blocks:
            blocks.append(TextBlock(text=""))
        return Message(role="assistant", content=blocks)


class OpenAIResponsesProvider(ResponsesConverterMixin, BaseProvider):
    """OpenAI Responses API, including the ChatGPT subscription backend."""

    def __init__(
        self,
        model: str = "gpt-6.1-sol",
        api_key: str | None = None,
        base_url: str | None = None,
        max_retries: int = 3,
        default_headers: dict[str, str] | None = None,
        store: bool = False,
        stream_only: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model, api_key=api_key)
        self._max_retries = max_retries
        self._store = store
        # The ChatGPT subscription backend answers a non-streaming request with
        # 400 "Stream must be set to true"; chat() then collects the stream.
        self._stream_only = stream_only
        self.base_url = base_url
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("Install openai: pip install openai")

        client_kwargs: dict[str, Any] = dict(kwargs)
        # The SDK sends `Authorization: Bearer <api_key>`, which is exactly what
        # the subscription backend wants — the access token goes in as api_key.
        client_kwargs["api_key"] = api_key or ""
        if base_url:
            client_kwargs["base_url"] = base_url
        if default_headers:
            client_kwargs["default_headers"] = default_headers
        self._client = AsyncOpenAI(**client_kwargs)

    def _api_kwargs(
        self, messages: list[Message], tools: list[ToolDef] | None, system_prompt: str, kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        converted = self.convert_messages(messages, system_prompt)
        # The runner speaks chat-completions kwargs; the Responses API nests
        # effort under `reasoning` and calls the output cap `max_output_tokens`.
        effort = kwargs.pop("reasoning_effort", None)
        if effort:
            kwargs.setdefault("reasoning", {"effort": effort})
        if "max_tokens" in kwargs:
            kwargs.setdefault("max_output_tokens", kwargs.pop("max_tokens"))
        api_kwargs: dict[str, Any] = {
            "model": self.model,
            "input": converted["input"],
            "store": kwargs.pop("store", self._store),
            "stream": kwargs.pop("stream", None),
            **kwargs,
        }
        if "instructions" in converted:
            api_kwargs["instructions"] = converted["instructions"]
        if tools:
            api_kwargs["tools"] = self.convert_tools(tools)
        return api_kwargs

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> Message:
        if self._stream_only:
            return await self._chat_via_stream(messages, tools, system_prompt, kwargs)

        retryable = get_provider_retryable_exceptions("openai")

        @with_retry(max_retries=self._max_retries, retryable_exceptions=retryable)
        async def _call() -> Message:
            api_kwargs = self._api_kwargs(messages, tools, system_prompt, dict(kwargs))
            api_kwargs.pop("stream", None)
            response = await self._client.responses.create(**api_kwargs)
            return self.convert_response(response)

        return await _call()

    async def _chat_via_stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None,
        system_prompt: str,
        kwargs: dict[str, Any],
    ) -> Message:
        text: list[str] = []
        calls: list[ContentBlock] = []
        async for event in self.stream(messages, tools, system_prompt, **kwargs):
            if isinstance(event, ProviderTextDelta):
                text.append(event.delta)
            elif isinstance(event, ProviderToolCall):
                calls.append(ToolUseBlock(id=event.id, name=event.name, arguments=event.arguments))
            elif isinstance(event, ProviderError):
                raise RuntimeError(event.error)
        blocks: list[ContentBlock] = [TextBlock(text="".join(text))] if text else []
        return Message(role="assistant", content=[*blocks, *calls] or [TextBlock(text="")])

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> AsyncIterator[ProviderStreamEvent]:
        """Stream provider events, matching the contract of the other adapters."""
        api_kwargs = self._api_kwargs(messages, tools, system_prompt, dict(kwargs))
        api_kwargs["stream"] = True

        fallback_message_id = f"msg_{uuid.uuid4().hex[:24]}"
        message_id: str | None = None
        started = False

        try:
            stream = await self._client.responses.create(**api_kwargs)
            async for event in stream:
                if message_id is None:
                    response_id = getattr(getattr(event, "response", None), "id", None)
                    message_id = response_id or fallback_message_id

                if not started:
                    started = True
                    yield ProviderMessageStarted(message_id=message_id)

                event_type = getattr(event, "type", None)

                if event_type == "response.output_text.delta":
                    delta = getattr(event, "delta", "")
                    if delta:
                        yield ProviderTextDelta(message_id=message_id, delta=delta)

                elif event_type in ("response.output_item.added", "response.output_item.done"):
                    item = getattr(event, "item", None)
                    if getattr(item, "type", None) != "function_call":
                        continue
                    arguments = getattr(item, "arguments", None)
                    try:
                        parsed = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
                    except json.JSONDecodeError:
                        # A truncated argument blob is unexecutable; skip it as
                        # the chat-completions adapter does rather than emit junk.
                        continue
                    yield ProviderToolCall(
                        message_id=message_id,
                        id=getattr(item, "call_id", None) or f"call_{uuid.uuid4().hex[:24]}",
                        name=getattr(item, "name", "") or "",
                        arguments=parsed,
                    )

                elif event_type == "response.completed":
                    completed = getattr(event, "response", None)
                    if getattr(completed, "id", None):
                        message_id = completed.id
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

    # close() comes from BaseProvider: the client is an AsyncOpenAI instance.
