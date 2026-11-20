"""Sessions running side by side on one connection."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from openagent.core.tool import tool
from openagent.service.client import ServiceClient
from openagent.service.protocol import TERMINAL_EVENT_TYPES, Method
from openagent.tools.builtin import todo_list, todo_write
from tests.service.test_background import harness, isolated_state, workdir  # noqa: F401
from tests.service.test_server import Harness
from tests.support.streaming_provider import (
    HangingProvider,
    ScriptedStreamProvider,
    text_turn,
    tool_turn,
)

TIMEOUT = 10.0


async def until_ended(client: ServiceClient, runs: set[str]) -> dict[str, list[Any]]:
    """Every run's events, until each of ``runs`` has ended."""
    seen: dict[str, list[Any]] = {run: [] for run in runs}
    waiting = set(runs)
    async with asyncio.timeout(TIMEOUT):
        while waiting:
            event = await client._events.get()
            seen.setdefault(event.run_id, []).append(event)
            if event.type in TERMINAL_EVENT_TYPES:
                waiting.discard(event.run_id)
    return seen


def replies(*scripts: list[list[Any]]):
    queue = iter(scripts)
    return lambda ref: ScriptedStreamProvider(next(queue))


class GatedProvider(ScriptedStreamProvider):
    """Holds its first turn until a test lets it go."""

    def __init__(self, turns, gate: asyncio.Event) -> None:
        super().__init__(turns)
        self.gate = gate

    async def stream(self, *args: Any, **kwargs: Any):
        await self.gate.wait()
        async for event in super().stream(*args, **kwargs):
            yield event


async def test_two_sessions_run_at_once_on_one_connection(harness: Harness, workdir: Path):
    gate = asyncio.Event()
    built = iter([GatedProvider([text_turn("slow answer")], gate), ScriptedStreamProvider([text_turn("fast answer")])])
    service = await harness.serve(provider_factory=lambda ref: next(built))
    client = await harness.connect(service)

    slow = await client.start_run("slow", workdir)
    fast = await client.start_run("fast", workdir)
    assert slow["session_id"] != fast["session_id"]

    finished = await until_ended(client, {fast["run_id"]})
    assert finished[fast["run_id"]][-1].result.output_text == "fast answer"
    assert not any(e.type in TERMINAL_EVENT_TYPES for e in finished.get(slow["run_id"], []))

    gate.set()
    rest = await until_ended(client, {slow["run_id"]})
    assert rest[slow["run_id"]][-1].result.output_text == "slow answer"


async def test_cancelling_one_session_leaves_the_other_running(harness: Harness, workdir: Path):
    gate = asyncio.Event()
    built = iter([HangingProvider(), GatedProvider([text_turn("survived")], gate)])
    service = await harness.serve(provider_factory=lambda ref: next(built))
    client = await harness.connect(service)
    doomed = await client.start_run("a", workdir)
    kept = await client.start_run("b", workdir)

    await client.cancel(doomed["run_id"])
    ended = await until_ended(client, {doomed["run_id"]})
    assert ended[doomed["run_id"]][-1].type == "run_cancelled"
    gate.set()
    rest = await until_ended(client, {kept["run_id"]})
    assert rest[kept["run_id"]][-1].type == "run_completed"


async def test_a_run_ending_does_not_refuse_another_runs_prompt(harness: Harness, workdir: Path):
    @tool
    def risky(text: str) -> str:
        """Unknown to the classifier, so it asks."""
        return f"did:{text}"

    service = await harness.serve(
        tools=[risky],
        permission_timeout=TIMEOUT,
        provider_factory=replies(
            [tool_turn("risky", {"text": "x"}), text_turn("approved run done")],
            [text_turn("quick run done")],
        ),
    )
    client = await harness.connect(service)
    asking = await client.start_run("a", workdir)
    async with asyncio.timeout(TIMEOUT):
        while (prompt := await client._events.get()).type != "permission_requested":
            pass
    quick = await client.start_run("b", workdir)
    await until_ended(client, {quick["run_id"]})

    await client.respond_permission(prompt.request_id, True, "once")
    seen = await until_ended(client, {asking["run_id"]})
    assert "tool_call_completed" in [e.type for e in seen[asking["run_id"]]]


async def test_only_one_session_at_a_time_has_the_browser(harness: Harness, workdir: Path):
    active = 0
    most = 0

    @tool(name="browser_click")
    async def click(text: str) -> str:
        """Act on the page."""
        nonlocal active, most
        active += 1
        most = max(most, active)
        await asyncio.sleep(0.05)
        active -= 1
        return "clicked"

    turn = [tool_turn("browser_click", {"text": "x"}), tool_turn("browser_click", {"text": "y"}, id="call_2"), text_turn("done")]
    service = await harness.serve(
        tools=[click], provider_factory=replies(list(turn), list(turn))
    )
    client = await harness.connect(service)
    first = await client.start_run("a", workdir, permission_mode="allow_all")
    second = await client.start_run("b", workdir, permission_mode="allow_all")
    seen = await until_ended(client, {first["run_id"], second["run_id"]})
    assert all(events[-1].type == "run_completed" for events in seen.values())
    assert most == 1
    assert not service._device_lock.locked()


async def test_each_session_keeps_its_own_todo_list(harness: Harness, workdir: Path):
    tasks = [{"subject": "only in the first session", "description": "d"}]
    service = await harness.serve(
        tools=[todo_write, todo_list],
        provider_factory=replies(
            [tool_turn("todo_write", {"tasks": tasks}), text_turn("noted")],
            [tool_turn("todo_list", {}), text_turn("listed")],
        ),
    )
    client = await harness.connect(service)
    first = await client.start_run("a", workdir, permission_mode="allow_all")
    await until_ended(client, {first["run_id"]})
    second = await client.start_run("b", workdir, permission_mode="allow_all")
    seen = await until_ended(client, {second["run_id"]})
    listing = next(e for e in seen[second["run_id"]] if e.type == "tool_call_completed").result
    assert "only in the first session" not in listing
