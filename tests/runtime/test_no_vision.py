"""A model that cannot read images is not sent any.

llama.cpp without an mmproj answers an image with a 500 ("image input is not
supported"), and one image anywhere in the history fails every later request.
"""

from __future__ import annotations

from typing import Any

import pytest

from openagent.core.tool import tool
from openagent.core.types import ImageBlock, Message, ToolResultBlock
from openagent.runtime.loop import NO_VISION_IMAGE_TEXT, NO_VISION_NOTE, refuses_images
from openagent.runtime.runner import AgentRunner
from openagent.tools import desktop
from tests.support.streaming_provider import ScriptedStreamProvider, error_turn, text_turn, tool_turn

LLAMA_CPP_REFUSAL = (
    "Error code: 500 - {'error': {'code': 500, 'message': 'image input is not supported - "
    "hint: if this is unexpected, you may need to provide the mmproj', 'type': 'server_error'}}"
)


class TextOnlyProvider(ScriptedStreamProvider):
    """Refuses any request with an image in it, as llama.cpp does without an mmproj."""

    def __init__(self, turns: list[list[Any]]) -> None:
        super().__init__(turns)
        self.refused = 0

    async def stream(self, messages: list[Message], tools=None, system_prompt: str = "", **kwargs: Any):
        if any(_images(m) for m in messages):
            self.refused += 1
            for event in error_turn(LLAMA_CPP_REFUSAL):
                yield event
            return
        async for event in super().stream(messages, tools, system_prompt, **kwargs):
            yield event


def _images(message: Message) -> int:
    return sum(isinstance(b, ImageBlock) for b in message.content) if isinstance(message.content, list) else 0


@tool
def desktop_screenshot() -> str:
    """Capture the app's window."""
    desktop._last_screenshot = ("WINDOWBYTES", "image/jpeg")
    return "Captured the window of Finder."


@pytest.fixture(autouse=True)
def no_capture(monkeypatch):
    monkeypatch.setattr(desktop, "_last_screenshot", None)


async def run(runner: AgentRunner, prompt: str = "go", **kwargs: Any) -> list[Any]:
    return [event async for event in runner.stream(prompt, **kwargs)]


def _tool_result(runner: AgentRunner) -> ToolResultBlock:
    return next(
        b for m in runner.session.messages if isinstance(m.content, list) for b in m.content
        if isinstance(b, ToolResultBlock)
    )


async def test_a_refused_screenshot_is_taken_out_and_the_turn_asked_again():
    provider = TextOnlyProvider([tool_turn("desktop_screenshot", {}), text_turn("Read the files instead.")])
    runner = AgentRunner(provider, tools=[desktop_screenshot])

    events = await run(runner)

    assert events[-1].type == "run_completed"
    assert provider.refused == 1
    assert "cannot read images" in next(e for e in events if e.type == "message_failed").error
    assert not runner.vision
    assert not any(_images(m) for m in runner.session.messages)
    assert any(NO_VISION_IMAGE_TEXT in str(m.content) for m in runner.session.messages)


async def test_a_blind_run_attaches_no_screenshot_and_says_why():
    provider = TextOnlyProvider([tool_turn("desktop_screenshot", {}), text_turn("ok")])
    runner = AgentRunner(provider, tools=[desktop_screenshot], vision=False)

    events = await run(runner)

    assert events[-1].type == "run_completed"
    assert provider.refused == 0
    assert _tool_result(runner).content == f"Captured the window of Finder. {NO_VISION_NOTE}"


async def test_a_blind_run_takes_out_the_persons_image():
    provider = TextOnlyProvider([text_turn("ok")])
    runner = AgentRunner(provider, vision=False)

    events = await run(runner, "what is this?", images=["PASTEDBYTES"])

    assert events[-1].type == "run_completed"
    assert provider.refused == 0
    assert NO_VISION_IMAGE_TEXT in str(runner.session.messages[0].content)


async def test_a_refusal_with_no_image_to_take_out_fails_the_run():
    provider = ScriptedStreamProvider([error_turn(LLAMA_CPP_REFUSAL)])
    runner = AgentRunner(provider)

    events = await run(runner)

    assert events[-1].type == "run_failed"
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    "error, refused",
    [
        (LLAMA_CPP_REFUSAL, True),
        ("The model qwen3 is not a multimodal model", True),
        ("Model does not support images. Please use a model that does.", True),
        ("upstream 503", False),
        ("context length exceeded", False),
    ],
)
def test_refuses_images(error, refused):
    assert refuses_images(error) is refused
