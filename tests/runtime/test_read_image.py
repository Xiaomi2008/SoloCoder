"""``read`` on an image file shows the model the picture.

Before, it returned only a summary: a model that rendered a sprite to PNG to
look at it saw none of 25 renders and judged the result from pixel dumps.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from openagent.core.session import Session
from openagent.core.tool import ToolContext
from openagent.core.types import ImageBlock, Message, ToolResultBlock, ToolUseBlock
from openagent.runtime import loop
from openagent.runtime.loop import KEEP_RECENT_SCREENSHOTS, add_read_image_followup, is_user_prompt
from openagent.runtime.runner import AgentRunner
from openagent.tools.builtin import read
from tests.support.streaming_provider import ScriptedStreamProvider, text_turn, tool_turn

PNG = b"\x89PNG\r\n\x1a\n not a real picture"


@pytest.fixture
def sprite(tmp_path: Path) -> Path:
    path = tmp_path / "sprite.png"
    path.write_bytes(PNG)
    return path


def _images(messages: list[Message]) -> list[ImageBlock]:
    return [b for m in messages if isinstance(m.content, list) for b in m.content if isinstance(b, ImageBlock)]


def _read_result(session: Session) -> ToolResultBlock:
    return next(b for m in session.messages if isinstance(m.content, list) for b in m.content
                if isinstance(b, ToolResultBlock))


async def run(runner: AgentRunner) -> list[Any]:
    return [event async for event in runner.stream("look at the sprite")]


async def test_a_read_image_is_shown_to_the_model(sprite: Path):
    provider = ScriptedStreamProvider([tool_turn("read", {"path": "sprite.png"}), text_turn("Short legs.")])
    runner = AgentRunner(provider, tools=[read], tool_context=ToolContext(working_dir=str(sprite.parent)))

    events = await run(runner)

    assert events[-1].type == "run_completed"
    [image] = _images(provider.calls[1]["messages"])
    assert image.mime_type == "image/png" and base64.b64decode(image.data) == PNG
    assert _read_result(runner.session).content.endswith("The image is attached in the next message.")
    assert not is_user_prompt(runner.session.messages[-2])


async def test_a_model_that_cannot_read_images_is_told_so(sprite: Path):
    provider = ScriptedStreamProvider([tool_turn("read", {"path": str(sprite)}), text_turn("ok")])
    runner = AgentRunner(provider, tools=[read], vision=False)

    await run(runner)

    assert not _images(provider.calls[1]["messages"])
    assert "cannot read images" in _read_result(runner.session).content


def _read(session: Session, path: Path, call_id: str) -> bool:
    call = ToolUseBlock(name="read", arguments={"path": str(path)}, id=call_id)
    result = ToolResultBlock(tool_use_id=call_id, tool_name="read", content=read(str(path)))
    session.add_tool_results([result])
    return add_read_image_followup(session, [result], [call])


def test_a_picture_too_big_to_send_is_not_attached(sprite: Path, monkeypatch):
    monkeypatch.setattr(loop, "MAX_READ_IMAGE_BYTES", 4)
    session = Session()

    assert not _read(session, sprite, "c1")
    assert "Too large to show" in _read_result(session).content


def test_only_the_recent_read_images_stay_attached(sprite: Path):
    session = Session()
    for n in range(KEEP_RECENT_SCREENSHOTS + 2):
        assert _read(session, sprite, f"c{n}")

    assert len(_images(session.messages)) == KEEP_RECENT_SCREENSHOTS


def test_an_svg_is_not_attached(tmp_path: Path):
    svg = tmp_path / "icon.svg"
    svg.write_text("<svg/>")
    session = Session()

    assert not _read(session, svg, "c1")
    assert not _images(session.messages)
