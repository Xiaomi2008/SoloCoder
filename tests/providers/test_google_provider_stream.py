from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from openagent.core.types import Message
from openagent.provider.google import GoogleProvider
from openagent.providers import (
    ProviderError,
    ProviderMessageCompleted,
    ProviderMessageStarted,
    ProviderStreamEvent,
    ProviderTextDelta,
    ProviderToolCall,
)


class FakeGenerateContentConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakePart:
    @staticmethod
    def from_text(text: str) -> SimpleNamespace:
        return SimpleNamespace(text=text)


class FakeContent:
    def __init__(self, role: str, parts: list[SimpleNamespace]):
        self.role = role
        self.parts = parts


class FakeModels:
    async def generate_content_stream(self, **kwargs):
        self.last_kwargs = kwargs

        async def _stream():
            yield SimpleNamespace(text="Hello")
            yield SimpleNamespace(text=" world")

        return _stream()


class FakeStreamingModels:
    def __init__(self, chunks):
        self._chunks = chunks

    async def generate_content_stream(self, **kwargs):
        self.last_kwargs = kwargs

        async def _stream():
            for chunk in self._chunks:
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk

        return _stream()


def install_fake_google_genai(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_types = SimpleNamespace(
        Content=FakeContent,
        GenerateContentConfig=FakeGenerateContentConfig,
        Part=FakePart,
    )
    fake_genai = ModuleType("google.genai")
    fake_genai.types = fake_types

    google_module = ModuleType("google")
    google_module.genai = fake_genai

    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)


@pytest.mark.asyncio
async def test_google_provider_stream_yields_provider_events_for_text(
    monkeypatch: pytest.MonkeyPatch,
):
    install_fake_google_genai(monkeypatch)
    provider = GoogleProvider.__new__(GoogleProvider)
    provider.model = "gemini-test"
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=FakeModels()))

    events = [
        event
        async for event in provider.stream(
            messages=[Message(role="user", content="Hi")],
        )
    ]

    assert [type(event) for event in events] == [
        ProviderMessageStarted,
        ProviderTextDelta,
        ProviderTextDelta,
        ProviderMessageCompleted,
    ]
    assert all(isinstance(event, ProviderStreamEvent) for event in events)
    assert events[1].delta == "Hello"
    assert events[2].delta == " world"
    assert len({event.message_id for event in events}) == 1


@pytest.mark.asyncio
async def test_google_provider_stream_emits_provider_tool_call_for_function_call_chunks(
    monkeypatch: pytest.MonkeyPatch,
):
    install_fake_google_genai(monkeypatch)
    provider = GoogleProvider.__new__(GoogleProvider)
    provider.model = "gemini-test"
    provider._client = SimpleNamespace(
        aio=SimpleNamespace(
            models=FakeStreamingModels(
                [
                    SimpleNamespace(
                        text=None,
                        candidates=[
                            SimpleNamespace(
                                content=SimpleNamespace(
                                    parts=[
                                        SimpleNamespace(
                                            text=None,
                                            function_call=SimpleNamespace(
                                                name="lookup_weather",
                                                args={
                                                    "city": "Paris",
                                                    "units": "metric",
                                                },
                                            ),
                                        )
                                    ]
                                )
                            )
                        ],
                    )
                ]
            )
        )
    )

    events = [
        event
        async for event in provider.stream(
            messages=[Message(role="user", content="Hi")],
        )
    ]

    assert [type(event) for event in events] == [
        ProviderMessageStarted,
        ProviderToolCall,
        ProviderMessageCompleted,
    ]
    assert events[1].message_id == events[0].message_id
    assert events[1].name == "lookup_weather"
    assert events[1].arguments == {"city": "Paris", "units": "metric"}


@pytest.mark.asyncio
async def test_google_provider_stream_emits_terminal_provider_error_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
):
    install_fake_google_genai(monkeypatch)
    provider = GoogleProvider.__new__(GoogleProvider)
    provider.model = "gemini-test"
    provider._client = SimpleNamespace(
        aio=SimpleNamespace(models=FakeStreamingModels([RuntimeError("stream broke")]))
    )

    events = [
        event
        async for event in provider.stream(
            messages=[Message(role="user", content="Hi")],
        )
    ]

    assert [type(event) for event in events] == [
        ProviderMessageStarted,
        ProviderError,
    ]
    assert events[1].message_id == events[0].message_id
    assert events[1].error == "stream broke"
