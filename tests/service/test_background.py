"""Long tool calls move to the background, and the agent is called back.

Real sockets and real shell commands, with the 25 seconds cut to a fraction so
the suite stays fast.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from openagent.core.types import Message
from openagent.service.client import ServiceClient
from openagent.service.protocol import Method
from openagent.tools.builtin import bash, bash_output, kill_shell
from tests.service.test_server import Harness, collect_events
from tests.support.streaming_provider import ScriptedStreamProvider, text_turn, tool_turn

TIMEOUT = 10.0
AFTER = 0.15


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


async def serve(harness: Harness, *runs: list[list[Any]]) -> ServiceClient:
    """A service whose Nth run replays the Nth script."""
    scripts = iter(runs)

    def build(ref):
        provider = ScriptedStreamProvider(next(scripts))
        harness.providers.append(provider)
        return provider

    service = await harness.serve(
        tools=[bash, bash_output, kill_shell],
        provider_factory=build,
        background_after=AFTER,
    )
    return await harness.connect(service)


async def start(client: ServiceClient, workdir: Path, **params: Any) -> dict[str, Any]:
    return await client.start_run(params.pop("prompt", "go"), workdir, permission_mode="allow_all", **params)


async def resume(client: ServiceClient, workdir: Path, session_id: str) -> dict[str, Any]:
    return await client.call(
        Method.RUN_START,
        {
            "resume_background": True,
            "session_id": session_id,
            "working_dir": str(workdir),
            "permission_mode": "allow_all",
        },
    )


async def next_event(client: ServiceClient, kind: str) -> Any:
    async with asyncio.timeout(TIMEOUT):
        while True:
            event = await client._events.get()
            if getattr(event, "type", None) == kind:
                return event


def types(events: list[Any]) -> list[str]:
    return [event.type for event in events]


def of(events: list[Any], kind: str) -> Any:
    return next(event for event in events if event.type == kind)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def wait_until(condition) -> None:
    async with asyncio.timeout(TIMEOUT):
        while not condition():
            await asyncio.sleep(0.02)


def seen_text(message: Message) -> str:
    if isinstance(message.content, str):
        return message.content
    return "\n".join(getattr(block, "content", getattr(block, "text", "")) for block in message.content)


async def test_a_long_command_frees_the_run_and_calls_the_agent_back(harness, workdir):
    client = await serve(
        harness,
        [tool_turn("bash", {"command": "sleep 0.5; echo built"}), text_turn("it is building")],
        [text_turn("the build passed")],
    )
    started = await start(client, workdir)
    events = await collect_events(client)

    assert "background_task_started" in types(events)
    assert "tool_call_completed" not in types(events)
    assert events[-1].type == "run_completed"
    placeholder = seen_text(harness.providers[0].calls[1]["messages"][-1])
    assert "Do not run it again" in placeholder

    finished = await next_event(client, "background_task_finished")
    assert (finished.state, finished.wake) == ("completed", True)
    assert finished.output == "built\n"
    assert finished.tool_call_id == of(events, "background_task_started").tool_call_id

    woken = await resume(client, workdir, started["session_id"])
    assert woken["run_id"]
    events = await collect_events(client)
    delivered = of(events, "background_tasks_delivered")
    assert delivered.resumed is True
    assert "sleep 0.5" in delivered.summary
    assert events[-1].result.output_text == "the build passed"
    told = harness.providers[1].calls[0]["messages"][-1]
    assert told.role == "user"
    assert told.text.startswith("[Background task]")
    assert "built" in told.text

    # Told once: there is nothing left to call back for.
    assert (await resume(client, workdir, started["session_id"]))["run_id"] is None


async def test_the_user_can_ask_for_progress_while_it_runs(harness, workdir):
    client = await serve(
        harness,
        [tool_turn("bash", {"command": "echo step-one; sleep 30"}), text_turn("running")],
        [tool_turn("bash_output", {"session_id": "all"}), text_turn("it is on step one")],
    )
    started = await start(client, workdir)
    first = await collect_events(client)
    task_id = of(first, "background_task_started").task_id

    await start(client, workdir, prompt="how is it going?", session_id=started["session_id"])
    second = await collect_events(client)
    report = of(second, "tool_call_completed").result
    assert task_id in report and "running for" in report and "step-one" in report
    assert second[-1].result.output_text == "it is on step one"

    listed = await client.call(Method.BACKGROUND_LIST, {"session_id": started["session_id"]})
    assert [task["task_id"] for task in listed["tasks"]] == [task_id]
    assert listed["wake_due"] is False


async def test_a_result_waiting_when_the_user_asks_joins_that_prompt(harness, workdir):
    client = await serve(
        harness,
        [tool_turn("bash", {"command": "sleep 0.3; echo finished-output"}), text_turn("running")],
        [text_turn("answered both")],
    )
    started = await start(client, workdir)
    await collect_events(client)
    await next_event(client, "background_task_finished")

    await start(client, workdir, prompt="anything new?", session_id=started["session_id"])
    events = await collect_events(client)
    assert of(events, "background_tasks_delivered").resumed is False
    messages = harness.providers[1].calls[0]["messages"]
    assert messages[-2].role != "user"
    assert messages[-1].text.startswith("anything new?")
    assert "finished-output" in messages[-1].text


async def test_the_later_calls_of_a_batch_wait_for_the_model(harness, workdir):
    marker = workdir / "second-ran"
    turn = tool_turn("bash", {"command": "sleep 30"})
    second = tool_turn("bash", {"command": f"touch {marker}"}, id="call_2")
    client = await serve(harness, [[*turn[:-1], second[1], turn[-1]], text_turn("waiting")])
    await start(client, workdir)
    events = await collect_events(client)
    assert types(events).count("tool_call_started") == 1
    results = harness.providers[0].calls[1]["messages"][-1].tool_results
    assert "Not run" in results[1].content and results[1].is_error
    assert not marker.exists()


async def test_stopping_a_task_kills_it_and_does_not_call_back(harness, workdir):
    pid_file = workdir / "pid"
    client = await serve(
        harness,
        [tool_turn("bash", {"command": f"sh -c 'echo $$ > {pid_file}; sleep 30'"}), text_turn("running")],
    )
    started = await start(client, workdir)
    events = await collect_events(client)
    await wait_until(pid_file.exists)
    pid = int(pid_file.read_text())
    task_id = of(events, "background_task_started").task_id

    answer = await client.call(
        Method.BACKGROUND_CANCEL, {"session_id": started["session_id"], "task_id": task_id}
    )
    assert answer["cancelled"] is True
    finished = await next_event(client, "background_task_finished")
    assert (finished.state, finished.wake) == ("cancelled", False)
    await wait_until(lambda: not alive(pid))
    assert (await resume(client, workdir, started["session_id"]))["run_id"] is None


async def test_a_client_that_leaves_takes_its_tasks_with_it(harness, workdir):
    pid_file = workdir / "pid"
    client = await serve(
        harness,
        [tool_turn("bash", {"command": f"sh -c 'echo $$ > {pid_file}; sleep 30'"}), text_turn("running")],
    )
    await start(client, workdir)
    await collect_events(client)
    await wait_until(pid_file.exists)
    pid = int(pid_file.read_text())
    await client.close()
    await wait_until(lambda: not alive(pid))


async def test_stop_reaches_a_command_that_is_still_in_the_foreground(harness, workdir):
    pid_file = workdir / "pid"
    service = await harness.serve(
        tools=[bash],
        provider_factory=harness.script(
            [tool_turn("bash", {"command": f"sh -c 'echo $$ > {pid_file}; sleep 30'"})]
        ),
        background_after=30.0,
    )
    client = await harness.connect(service)
    started = await start(client, workdir)
    await wait_until(pid_file.exists)
    pid = int(pid_file.read_text())

    await client.call(Method.RUN_CANCEL, {"run_id": started["run_id"]})
    events = await collect_events(client)
    assert events[-1].type == "run_cancelled"
    await wait_until(lambda: not alive(pid))
    await wait_until(lambda: started["session_id"] not in service._live)
    stored = await client.call(Method.SESSION_LOAD, {"session_id": started["session_id"]})
    assert stored["messages"][-1]["role"] == "tool_result"


async def test_a_quick_command_is_an_ordinary_tool_call(harness, workdir):
    client = await serve(harness, [tool_turn("bash", {"command": "echo quick"}), text_turn("done")])
    await start(client, workdir)
    events = await collect_events(client)
    assert of(events, "tool_call_completed").result == "quick\n"
    assert "background_task_started" not in types(events)
