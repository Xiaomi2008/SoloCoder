"""A model whose server refused an image is sent none in its later runs."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.runtime.test_no_vision import TextOnlyProvider
from tests.service.test_server import Harness, collect_events
from tests.support.streaming_provider import text_turn


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


async def test_the_next_run_sends_no_image(harness: Harness, tmp_path: Path):
    def build(ref):
        provider = TextOnlyProvider([text_turn("ok")])
        harness.providers.append(provider)
        return provider

    client = await harness.connect(await harness.serve(tools=[], provider_factory=build))
    for _ in range(2):
        await client.start_run("what is this?", tmp_path, permission_mode="allow_all", images=["PASTED"])
        events = await collect_events(client)
        assert events[-1].type == "run_completed"

    assert [p.refused for p in harness.providers] == [1, 0]
