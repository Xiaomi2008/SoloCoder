"""Every provider sends a user message's images, not just its text."""

from __future__ import annotations

import base64

import pytest

from openagent.core.session import Session
from openagent.provider.anthropic import AnthropicConverterMixin
from openagent.provider.ollama import OllamaConverterMixin

IMAGES = [base64.b64encode(b"one").decode(), base64.b64encode(b"two").decode()]


def _messages():
    session = Session()
    session.add_user_multimodal(text="Compare these", images=IMAGES)
    return session.messages


def test_anthropic_sends_image_blocks():
    converted = AnthropicConverterMixin().convert_messages(_messages())
    content = converted["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Compare these"}
    assert [b["source"]["data"] for b in content[1:]] == IMAGES
    assert all(b["type"] == "image" and b["source"]["media_type"] == "image/png" for b in content[1:])


def test_ollama_sends_images_field():
    converted = OllamaConverterMixin().convert_messages(_messages())
    user = converted["messages"][0]
    assert user["content"] == "Compare these"
    assert user["images"] == IMAGES


def test_google_sends_inline_image_parts():
    pytest.importorskip("google.genai")
    from openagent.provider.google import GoogleConverterMixin

    converted = GoogleConverterMixin().convert_messages(_messages())
    parts = converted["contents"][0].parts
    assert parts[0].text == "Compare these"
    assert [p.inline_data.data for p in parts[1:]] == [b"one", b"two"]
