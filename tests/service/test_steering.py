"""A message typed while the agent works reaches it on its next turn."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from openagent.service.client import ServiceClient
from openagent.tools.builtin import bash
from tests.service.test_server import Harness, carried_history, collect_events
from tests.support.streaming_provider import ScriptedStreamProvider, text_turn, tool_turn


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))


@pytest.fixture
async def harness(tmp_path: Path):
    runner = Harness(tmp_path)
    try:
        yield runner
    finally:
        await runner.aclose()


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


async def serve(harness: Harness, script: list[list[Any]]) -> ServiceClient:
    def build(ref):
        provider = ScriptedStreamProvider(script)
        harness.providers.append(provider)
        return provider

    return await harness.connect(await harness.serve(tools=[bash], provider_factory=build))


async def test_a_message_sent_during_a_tool_call_reaches_the_next_turn(harness: Harness, workdir: Path):
    client = await serve(
        harness,
        [tool_turn("bash", {"command": "sleep 0.5"}, id="c1"), text_turn("using the other file")],
    )
    started = await client.start_run("go", workdir, permission_mode="allow_all")
    events_task = asyncio.create_task(collect_events(client))
    await asyncio.sleep(0.2)

    answer = await client.steer(started["run_id"], "use the other file")
    events = await events_task

    assert answer["accepted"] is True
    types = [e.type for e in events]
    delivered = next(e for e in events if e.type == "steering_delivered")
    assert delivered.steer_id == answer["steer_id"]
    assert delivered.text == "use the other file"
    assert types.index("tool_call_completed") < types.index("steering_delivered")
    assert types[-1] == "run_completed"
    second = carried_history(harness.providers[0].calls[1]["messages"])
    assert "use the other file" in second, "the model's next turn carries the message"


async def test_a_message_sent_during_the_last_answer_keeps_the_run_going(harness: Harness, workdir: Path):
    client = await serve(harness, [text_turn("done"), text_turn("ok, the other file then")])
    provider_gate = asyncio.Event()
    original = ScriptedStreamProvider.stream

    async def slow_stream(self, *args, **kwargs):
        if not provider_gate.is_set():
            await asyncio.sleep(0.3)
        async for event in original(self, *args, **kwargs):
            yield event

    ScriptedStreamProvider.stream = slow_stream
    try:
        started = await client.start_run("go", workdir, permission_mode="allow_all")
        events_task = asyncio.create_task(collect_events(client))
        await asyncio.sleep(0.1)
        answer = await client.steer(started["run_id"], "actually, the other file")
        provider_gate.set()
        events = await events_task
    finally:
        ScriptedStreamProvider.stream = original

    assert answer["accepted"] is True
    assert len(harness.providers[0].calls) == 2, "the run took another turn for it"
    assert [e.type for e in events].count("message_completed") == 2
    assert events[-1].type == "run_completed"


async def test_a_message_for_a_finished_run_is_refused(harness: Harness, workdir: Path):
    client = await serve(harness, [text_turn("done")])
    started = await client.start_run("go", workdir, permission_mode="allow_all")
    await collect_events(client)

    answer = await client.steer(started["run_id"], "too late")

    assert answer == {"run_id": started["run_id"], "accepted": False, "steer_id": None}
