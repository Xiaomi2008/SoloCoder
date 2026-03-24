from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from openagent.core.types import Message, ToolResultBlock
from openagent.provider.google import GoogleProvider


class FakePart:
    @staticmethod
    def from_text(text: str) -> SimpleNamespace:
        return SimpleNamespace(kind="text", text=text)

    @staticmethod
    def from_function_response(name: str, response: dict[str, str]) -> SimpleNamespace:
        return SimpleNamespace(
            kind="function_response",
            function_response=SimpleNamespace(name=name, response=response),
        )


class FakeContent:
    def __init__(self, role: str, parts: list[SimpleNamespace]):
        self.role = role
        self.parts = parts


def install_fake_google_genai(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_types = SimpleNamespace(
        Content=FakeContent,
        Part=FakePart,
    )
    fake_genai = ModuleType("google.genai")
    fake_genai.types = fake_types

    google_module = ModuleType("google")
    google_module.genai = fake_genai

    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)


def test_google_provider_convert_messages_uses_tool_name_for_function_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_google_genai(monkeypatch)
    provider = GoogleProvider.__new__(GoogleProvider)

    result = provider.convert_messages(
        messages=[
            Message(
                role="tool_result",
                content=[
                    ToolResultBlock(
                        tool_use_id="call_123",
                        tool_name="lookup_weather",
                        content="22C and sunny",
                    )
                ],
            )
        ]
    )

    function_response = result["contents"][0].parts[0].function_response
    assert function_response.name == "lookup_weather"
    assert function_response.response == {"result": "22C and sunny"}
