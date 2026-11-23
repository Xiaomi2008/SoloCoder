"""A model that reads video watches a file through view_video.

The video rides in the history as its path and is read into a data: URL when
a request is sent, because basalt reads a path only for its own machine.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from openagent.apps.solocoder.agent import build_solocoder_tools
from openagent.core.session import Session
from openagent.core.tool import ToolContext
from openagent.core.types import Message, TextBlock, VideoBlock
from openagent.provider.openai import OpenAIConverterMixin
from openagent.provider.registry import spec_from_config
from openagent.runtime.loop import KEEP_RECENT_VIDEOS, drop_old_videos, is_user_prompt
from openagent.runtime.runner import AgentRunner
from openagent.tools import video
from openagent.tools.video import view_video
from tests.support.streaming_provider import ScriptedStreamProvider, text_turn, tool_turn

CLIP = b"\x00\x00\x00\x18ftypmp42 not really a movie"


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(CLIP)
    return path


def _videos(messages: list[Message]) -> list[VideoBlock]:
    return [b for m in messages if isinstance(m.content, list) for b in m.content if isinstance(b, VideoBlock)]


async def run(runner: AgentRunner, prompt: str = "go") -> list[Any]:
    return [event async for event in runner.stream(prompt)]


async def test_a_watched_video_is_sent_to_the_model_as_a_data_url(clip: Path):
    provider = ScriptedStreamProvider(
        [tool_turn("view_video", {"path": "clip.mp4", "fps": 1}), text_turn("A clip.")]
    )
    runner = AgentRunner(
        provider, tools=[view_video], tool_context=ToolContext(working_dir=str(clip.parent)), video=True
    )

    events = await run(runner)

    assert events[-1].type == "run_completed"
    [sent] = _videos(provider.calls[1]["messages"])
    assert sent.path == str(clip.resolve()) and sent.fps == 1.0
    wire = OpenAIConverterMixin().convert_messages(provider.calls[1]["messages"])["messages"]
    [part] = [p for m in wire if isinstance(m["content"], list) for p in m["content"] if p["type"] == "video_url"]
    assert part == {
        "type": "video_url",
        "video_url": {"url": "data:video/mp4;base64," + base64.b64encode(CLIP).decode()},
        "fps": 1.0,
    }
    followup = next(m for m in runner.session.messages if _videos([m]))
    assert not is_user_prompt(followup)


async def test_a_model_without_video_is_sent_none(clip: Path):
    session = Session()
    session.add_message(Message(role="user", content=[TextBlock(text="look"), VideoBlock(path=str(clip))]))
    provider = ScriptedStreamProvider([text_turn("ok")])
    runner = AgentRunner(provider, session=session)

    await run(runner)

    assert not _videos(provider.calls[0]["messages"])
    assert str(clip) in str(provider.calls[0]["messages"][0].content)


@pytest.mark.parametrize(
    "name, arguments, error",
    [
        ("missing.mp4", {}, "is not a file"),
        ("notes.txt", {}, "is not a video"),
        ("clip.mp4", {"fps": 120}, "fps must be"),
        ("clip.mp4", {"max_frames": 2}, "max_frames must be"),
    ],
)
def test_view_video_says_why_it_cannot_show_a_file(clip: Path, name, arguments, error):
    (clip.parent / "notes.txt").write_text("hi")
    result = view_video(name, tool_context=ToolContext(working_dir=str(clip.parent)), **arguments)
    assert result.startswith("Error") and error in result


def test_view_video_refuses_a_file_too_big_to_send(clip: Path, monkeypatch):
    monkeypatch.setattr(video, "MAX_VIDEO_BYTES", 4)
    assert "over the" in view_video(str(clip))


def test_view_video_keeps_to_the_working_directory(clip: Path, tmp_path: Path):
    inside = tmp_path / "project"
    inside.mkdir()
    result = view_video(str(clip), tool_context=ToolContext(working_dir=str(inside), enforce_scope=True))
    assert "outside the working directory" in result


def test_only_the_recent_videos_stay_attached(clip: Path):
    session = Session()
    for _ in range(KEEP_RECENT_VIDEOS + 2):
        session.add_message(Message(role="user", content=[VideoBlock(path=str(clip))]))

    assert drop_old_videos(session) == 2
    assert len(_videos(session.messages)) == KEEP_RECENT_VIDEOS


def test_a_video_is_saved_as_its_path(clip: Path, tmp_path: Path):
    session = Session()
    session.add_message(Message(role="user", content=[VideoBlock(path=str(clip), fps=0.5, max_frames=64)]))

    session.save(tmp_path / "session.json")
    assert "base64" not in (tmp_path / "session.json").read_text()
    assert _videos(Session.load(tmp_path / "session.json").messages) == [
        VideoBlock(path=str(clip), fps=0.5, max_frames=64)
    ]


def test_a_video_whose_file_is_gone_is_said_in_words(tmp_path: Path):
    message = Message(role="user", content=[VideoBlock(path=str(tmp_path / "gone.mp4"))])
    [entry] = OpenAIConverterMixin().convert_messages([message])["messages"]
    assert entry["content"][0]["type"] == "text" and "gone.mp4" in entry["content"][0]["text"]


def test_view_video_is_offered_only_to_a_video_model():
    names = lambda tools: {t.__name__ for t in tools}  # noqa: E731
    assert "view_video" in names(build_solocoder_tools(computer_use=False, video=True))
    assert "view_video" not in names(build_solocoder_tools(computer_use=False))


def test_an_endpoint_says_its_model_reads_video():
    entry = {"name": "taopc", "base_url": "http://192.168.4.22:8150/v1", "vision": True, "video": True}
    assert spec_from_config(entry).default_caps.video
    assert not spec_from_config({**entry, "video": False}).default_caps.video
