from __future__ import annotations

from collections.abc import AsyncIterator
import uuid
from typing import Any

from openagent.provider.base import BaseProvider
from openagent.provider.converter import MessageConverterMixin
from openagent.core.retry import get_provider_retryable_exceptions, with_retry
from openagent.core.types import (
    ContentBlock,
    Message,
    TextBlock,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
)
from openagent.providers import (
    ProviderError,
    ProviderMessageCompleted,
    ProviderMessageStarted,
    ProviderStreamEvent,
    ProviderTextDelta,
    ProviderToolCall,
)


class GoogleConverterMixin(MessageConverterMixin):
    """Converts between canonical message format and Google Gemini's format."""

    def convert_messages(
        self, messages: list[Message], system_prompt: str = ""
    ) -> dict[str, Any]:
        from google.genai import types

        contents: list[types.Content] = []
        result: dict[str, Any] = {}

        if system_prompt:
            result["system_instruction"] = system_prompt

        for msg in messages:
            if msg.role == "system":
                existing = result.get("system_instruction", "")
                result["system_instruction"] = (
                    f"{existing}\n\n{msg.text}" if existing else msg.text
                )

            elif msg.role == "user":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=msg.text)],
                    )
                )

            elif msg.role == "assistant":
                parts: list[types.Part] = []
                if isinstance(msg.content, str):
                    parts.append(types.Part.from_text(text=msg.content))
                else:
                    for b in msg.content:
                        if isinstance(b, TextBlock):
                            parts.append(types.Part.from_text(text=b.text))
                        elif isinstance(b, ToolUseBlock):
                            parts.append(
                                types.Part.from_function_call(
                                    name=b.name,
                                    args=b.arguments,
                                )
                            )
                contents.append(types.Content(role="model", parts=parts))

            elif msg.role == "tool_result":
                parts = []
                if isinstance(msg.content, list):
                    for b in msg.content:
                        if isinstance(b, ToolResultBlock):
                            parts.append(
                                types.Part.from_function_response(
                                    name=b.tool_name or b.tool_use_id,
                                    response={"result": b.content},
                                )
                            )
                if parts:
                    contents.append(types.Content(role="user", parts=parts))

        result["contents"] = contents
        return result

    def convert_response(self, response: Any) -> Message:
        blocks: list[ContentBlock] = []
        candidate = response.candidates[0]

        for part in candidate.content.parts:
            if part.text is not None:
                blocks.append(TextBlock(text=part.text))
            elif part.function_call is not None:
                fc = part.function_call
                blocks.append(
                    ToolUseBlock(
                        name=fc.name,
                        arguments=dict(fc.args) if fc.args else {},
                    )
                )

        if len(blocks) == 1 and isinstance(blocks[0], TextBlock):
            return Message(role="assistant", content=blocks[0].text)
        return Message(role="assistant", content=blocks)

    def convert_tools(self, tools: list[ToolDef]) -> list[dict[str, Any]]:
        declarations = []
        for t in tools:
            params = dict(t.parameters)
            params.pop("additionalProperties", None)
            declarations.append(
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": params,
                }
            )
        return declarations


class GoogleProvider(GoogleConverterMixin, BaseProvider):
    def __init__(
        self,
        model: str = "gemini-2.0-flash",
        api_key: str | None = None,
        max_retries: int = 3,
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model, api_key=api_key, **kwargs)
        self._max_retries = max_retries
        try:
            from google import genai
        except ImportError:
            raise ImportError("Install google-genai: pip install google-genai")
        self._client = genai.Client(api_key=api_key, **kwargs)

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
        from google.genai import types

        retryable = get_provider_retryable_exceptions("google")

        @with_retry(max_retries=self._max_retries, retryable_exceptions=retryable)
        async def _call() -> Message:
            converted = self.convert_messages(messages, system_prompt)
            config_kwargs: dict[str, Any] = {**kwargs}
            if "system_instruction" in converted:
                config_kwargs["system_instruction"] = converted["system_instruction"]
            if tools:
                declarations = self.convert_tools(tools)
                config_kwargs["tools"] = [
                    types.Tool(function_declarations=declarations)
                ]

            response = await self._client.aio.models.generate_content(
                model=self.model,
                contents=converted["contents"],
                config=types.GenerateContentConfig(**config_kwargs),
            )
            return self.convert_response(response)

        return await _call()

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> AsyncIterator[ProviderStreamEvent]:
        """Stream provider events from Google."""
        from google.genai import types

        converted = self.convert_messages(messages, system_prompt)
        config_kwargs: dict[str, Any] = {**kwargs}
        if "system_instruction" in converted:
            config_kwargs["system_instruction"] = converted["system_instruction"]
        if tools:
            declarations = self.convert_tools(tools)
            config_kwargs["tools"] = [types.Tool(function_declarations=declarations)]

        message_id = f"msg_{uuid.uuid4().hex[:24]}"
        yield ProviderMessageStarted(message_id=message_id)

        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=self.model,
                contents=converted["contents"],
                config=types.GenerateContentConfig(**config_kwargs),
            )
            async for chunk in stream:
                if chunk.text:
                    yield ProviderTextDelta(message_id=message_id, delta=chunk.text)

                for candidate in getattr(chunk, "candidates", None) or []:
                    content = getattr(candidate, "content", None)
                    for part in getattr(content, "parts", None) or []:
                        function_call = getattr(part, "function_call", None)
                        if function_call is None:
                            continue

                        yield ProviderToolCall(
                            message_id=message_id,
                            id=f"call_{uuid.uuid4().hex[:24]}",
                            name=function_call.name,
                            arguments=dict(function_call.args)
                            if function_call.args
                            else {},
                        )
        except Exception as exc:
            yield ProviderError(message_id=message_id, error=str(exc))
            return

        yield ProviderMessageCompleted(message_id=message_id)
