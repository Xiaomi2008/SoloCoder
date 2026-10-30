"""A screenshot tool result reaches the model as the captured image."""

from __future__ import annotations

import pytest

from openagent.core.session import Session
from openagent.core.types import ImageBlock, ToolResultBlock
from openagent.runtime.loop import add_screenshot_followup
from openagent.tools import computer_use


@pytest.fixture
def captured(monkeypatch):
    monkeypatch.setattr(computer_use, "_last_screenshot_base64", "SCREENBYTES")
    monkeypatch.setattr(computer_use, "_last_screenshot_size", (512, 288))
    monkeypatch.setattr(computer_use, "_original_screenshot_size", (1512, 850))


def _followup_image(session: Session) -> ImageBlock:
    images = [b for b in session.messages[-1].content if isinstance(b, ImageBlock)]
    assert len(images) == 1
    return images[0]


def test_summary_result_still_attaches_the_capture(captured):
    session = Session()
    summary = "Screenshot captured: 512x288 pixels (from (1512, 850)), 11 base64 chars"
    results = [ToolResultBlock(tool_use_id="t1", tool_name="screenshot", content=summary)]
    session.add_tool_results(results)

    assert add_screenshot_followup(session, results)
    assert _followup_image(session).data == "SCREENBYTES"
    assert results[0].content == summary


def test_base64_result_is_cut_to_a_summary(captured):
    session = Session()
    results = [ToolResultBlock(tool_use_id="t1", tool_name="screenshot", content="SCREENBYTES")]
    session.add_tool_results(results)

    assert add_screenshot_followup(session, results)
    assert _followup_image(session).data == "SCREENBYTES"
    stored = session.messages[0].content[0]
    assert "SCREENBYTES" not in stored.content


def test_failed_screenshot_attaches_nothing(captured):
    session = Session()
    results = [ToolResultBlock(tool_use_id="t1", tool_name="screenshot", content="Error capturing screenshot: denied")]
    session.add_tool_results(results)

    assert not add_screenshot_followup(session, results)
    assert len(session.messages) == 1


@pytest.mark.parametrize(
    "region",
    [
        [900, 100, 1200, 700],
        "[900, 100, 1200, 700]",
        "900,100,1200,700",
        '{"x": 900, "y": 100, "width": 1200, "height": 700}',
    ],
)
def test_region_accepts_what_models_send(region):
    assert computer_use._parse_region(region) == (900, 100, 1200, 700)


@pytest.mark.parametrize("region", ["[1, 2, 3]", "[1, 2, 0, 4]", "the window"])
def test_region_rejects_what_is_not_a_rectangle(region):
    with pytest.raises(ValueError, match="four integers"):
        computer_use._parse_region(region)


def test_region_schema_is_an_array_of_integers():
    region = computer_use.screenshot._tool_parameters["properties"]["region"]
    assert region["type"] == "array"
    assert region["items"] == {"type": "integer"}


def _action(tool_name: str, note: bool = True) -> ToolResultBlock:
    from openagent.runtime.loop import _AFTER_ACTION_NOTE

    content = f"Clicked at (1, 1) with left button. {_AFTER_ACTION_NOTE}" if note else "Clicked. No screenshot after it: Error"
    return ToolResultBlock(tool_use_id=tool_name, tool_name=tool_name, content=content)


def test_an_action_attaches_the_capture_it_took(captured):
    session = Session()
    results = [_action("click")]
    session.add_tool_results(results)

    assert add_screenshot_followup(session, results)
    assert _followup_image(session).data == "SCREENBYTES"


def test_an_action_without_a_capture_attaches_nothing(captured):
    session = Session()
    results = [_action("click", note=False)]
    session.add_tool_results(results)

    assert not add_screenshot_followup(session, results)


def test_several_actions_in_a_turn_attach_one_image(captured):
    session = Session()
    results = [_action("click"), _action("type_text"), _action("key_combination")]
    session.add_tool_results(results)

    assert add_screenshot_followup(session, results)
    images = [b for m in session.messages if isinstance(m.content, list) for b in m.content if isinstance(b, ImageBlock)]
    assert len(images) == 1


def test_only_the_recent_screenshots_keep_their_images(captured):
    from openagent.runtime.loop import KEEP_RECENT_SCREENSHOTS, OLD_SCREENSHOT_TEXT

    session = Session()
    for _ in range(KEEP_RECENT_SCREENSHOTS + 2):
        results = [_action("click")]
        session.add_tool_results(results)
        add_screenshot_followup(session, results)

    with_images = [m for m in session.messages if isinstance(m.content, list) and any(isinstance(b, ImageBlock) for b in m.content)]
    assert len(with_images) == KEEP_RECENT_SCREENSHOTS
    stripped = [m for m in session.messages if m.text == OLD_SCREENSHOT_TEXT]
    assert len(stripped) == 2
