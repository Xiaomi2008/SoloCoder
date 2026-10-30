"""The macOS GUI tools: key names, coordinates, typing, permissions, the event loop."""

from __future__ import annotations

import asyncio
import inspect
import time

import pytest

pyautogui = pytest.importorskip("pyautogui")

from openagent.tools import computer_use as cu  # noqa: E402


@pytest.fixture
def recorded(monkeypatch):
    """Record pyautogui calls instead of moving the real mouse."""
    calls: list[tuple] = []
    for name in ("click", "doubleClick", "moveTo", "scroll", "hotkey", "press", "write"):
        monkeypatch.setattr(
            pyautogui, name, lambda *a, _n=name, **k: calls.append((_n, a, k))
        )
    monkeypatch.setattr(cu, "_accessibility_trusted", lambda: True)
    monkeypatch.setattr(cu, "_step_aside", lambda: None)
    monkeypatch.setattr(cu, "_last_screenshot_size", (512, 332))
    monkeypatch.setattr(cu, "_original_screenshot_size", (1512, 982))
    monkeypatch.setattr(cu, "_last_screenshot_origin", (0, 0))
    return calls


@pytest.mark.parametrize(
    ("keys", "names"),
    [
        ("cmd+c", ["command", "c"]),
        ("CMD+Shift+T", ["command", "shift", "t"]),
        ("cmd++", ["command", "+"]),
        ("return", ["enter"]),
        ("opt+tab", ["option", "tab"]),
        ("control+a", ["ctrl", "a"]),
        (["cmd", "space"], ["command", "space"]),
    ],
)
def test_keys_map_to_names_pyautogui_knows(keys, names):
    assert cu._parse_keys(keys) == names


def test_an_unknown_key_is_an_error_not_a_silent_no_op():
    with pytest.raises(ValueError, match="foo"):
        cu._parse_keys("cmd+foo")


async def test_key_combination_presses_each_key(recorded):
    result = await cu.key_combination("cmd+c")
    assert recorded == [("hotkey", ("command", "c"), {})]
    assert result == "Pressed key combination: command+c"


async def test_key_combination_reports_a_typo(recorded):
    result = await cu.key_combination("cmd+cc")
    assert result.startswith("Error")
    assert recorded == []


async def test_scroll_uses_screenshot_coordinates_like_click(recorded):
    await cu.scroll(256, 166, -3)
    await cu.click(256, 166)
    (_, scroll_args, scroll_kwargs), (_, _, click_kwargs) = recorded
    assert scroll_args == (-3,)
    assert (scroll_kwargs["x"], scroll_kwargs["y"]) == (click_kwargs["x"], click_kwargs["y"])
    assert scroll_kwargs["x"] > 700


async def test_type_text_sends_non_ascii_as_unicode(recorded, monkeypatch):
    typed: list[tuple[str, float]] = []
    monkeypatch.setattr(cu, "_post_unicode", lambda text, interval: typed.append((text, interval)))
    monkeypatch.setattr(cu.sys, "platform", "darwin")
    result = await cu.type_text("café 你好 😀")
    assert typed == [("café 你好 😀", 0.01)]
    assert result == "Typed: café 你好 😀"


def test_unicode_events_press_return_for_a_newline(monkeypatch):
    quartz = pytest.importorskip("Quartz")
    posted: list[str] = []
    pressed: list[str] = []
    monkeypatch.setattr(
        quartz, "CGEventPost",
        lambda tap, event: posted.append(quartz.CGEventKeyboardGetUnicodeString(event, 4, None, None)[1]),
    )
    monkeypatch.setattr(pyautogui, "press", pressed.append)
    cu._post_unicode("é\n😀", 0)
    # A down and an up event per character.
    assert posted == ["é", "é", "😀", "😀"]
    assert pressed == ["enter"]


async def test_missing_accessibility_is_reported_not_faked(recorded, monkeypatch):
    monkeypatch.setattr(cu, "_accessibility_trusted", lambda: False)
    result = await cu.click(10, 10)
    assert "Accessibility" in result and result.startswith("Error")
    assert recorded == []


async def test_missing_screen_recording_is_reported(monkeypatch):
    monkeypatch.setattr(cu, "_screen_capture_allowed", lambda: False)
    result = await cu.screenshot()
    assert "Screen Recording" in result and result.startswith("Error")


def test_gui_tools_are_async_so_they_do_not_block_the_service():
    for name in ("screenshot", "click", "double_click", "type_text", "key_combination",
                 "move_mouse", "scroll", "wait"):
        assert inspect.iscoroutinefunction(getattr(cu, name)), name


async def test_a_blocking_tool_leaves_the_event_loop_free(monkeypatch, recorded):
    monkeypatch.setattr(pyautogui, "moveTo", lambda *a, **k: time.sleep(0.3))
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.02)

    task = asyncio.create_task(ticker())
    await cu.move_mouse(10, 10)
    task.cancel()
    assert ticks > 5


def test_schemas_survive_the_thread_wrapper():
    params = cu.click._tool_parameters
    assert set(params["properties"]) == {"x", "y", "button"}
    assert params["properties"]["x"]["type"] == "integer"


def test_step_aside_is_a_no_op_outside_the_app(monkeypatch):
    monkeypatch.delenv("OPENAGENT_HOST_PID", raising=False)
    cu._step_aside()
