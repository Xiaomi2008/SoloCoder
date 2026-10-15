"""ModelCatalog: discovery parsing and the capability probe."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openagent.core.types import Message, TextBlock, ToolUseBlock
from openagent.provider.catalog import ModelCatalog, _parse_listing
from openagent.provider.model_ref import ModelRef, WireAPI
from openagent.provider.registry import ProviderRegistry


@pytest.fixture
def catalog() -> ModelCatalog:
    return ModelCatalog(ProviderRegistry())


# --------------------------------------------------------------- listing parse

def test_ollama_listing_uses_declared_capabilities():
    payload = {
        "models": [
            {
                "name": "qwen3-coder:30b",
                "details": {"parameter_count": 30_000_000_000, "context_length": 262144},
                "capabilities": ["completion", "tools"],
            },
            {
                "name": "llava:13b",
                "details": {"context_length": 4096},
                "capabilities": ["completion", "vision"],
            },
        ]
    }
    models = _parse_listing(WireAPI.OLLAMA_CHAT, payload)
    by_name = {m.model: m for m in models}
    assert by_name["qwen3-coder:30b"].caps.tools is True
    assert by_name["qwen3-coder:30b"].caps.context_window == 262144
    assert by_name["llava:13b"].caps.tools is False
    assert by_name["llava:13b"].caps.vision is True


def test_openai_compatible_listing_covers_lm_studio_mlx_and_vllm():
    payload = {"data": [{"id": "mlx-community/Qwen3-30B"}, {"id": "qwen3-coder-32b", "meta": {"context_length": 65536}}]}
    models = _parse_listing(WireAPI.OPENAI_CHAT, payload)
    assert {m.model for m in models} == {"mlx-community/Qwen3-30B", "qwen3-coder-32b"}
    assert models[1].caps.context_window == 65536


def test_unrecognised_payload_yields_nothing():
    assert _parse_listing(WireAPI.OPENAI_CHAT, {"unexpected": True}) == []
    assert _parse_listing(WireAPI.OLLAMA_CHAT, None) == []


# ------------------------------------------------------------------ discovery

class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, routes):
        self.routes = routes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        if url in self.routes:
            return FakeResponse(self.routes[url])
        raise ConnectionRefusedError("no server")


async def test_discover_collects_from_every_running_server(catalog, monkeypatch):
    import httpx

    routes = {
        "http://localhost:1234/v1/models": {"data": [{"id": "qwen3-coder-32b"}]},
        "http://localhost:11434/api/tags": {"models": [{"name": "qwen3-coder:30b", "capabilities": ["tools"]}]},
        # mlx and llama.cpp share a default port; whichever server is running answers.
        "http://localhost:8080/v1/models": {"data": [{"id": "mlx-model"}]},
    }
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeClient(routes))

    found = await catalog.discover()
    assert {"lmstudio", "ollama", "mlx", "llama_cpp"} <= set(found)
    assert found["lmstudio"][0].key == "lmstudio/qwen3-coder-32b"
    assert found["mlx"][0].key == "mlx/mlx-model"
    assert found["ollama"][0].is_local is True


async def test_discover_is_silent_when_nothing_is_running(catalog, monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeClient({}))
    assert await catalog.discover() == {}


def test_builtins_populate_the_catalog_offline(catalog):
    keys = {ref.key for ref in catalog.all_models()}
    assert "anthropic/claude-sonnet-5-5" in keys
    assert "openai_oauth/gpt-5-codex" in keys
    assert "mlx/mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit" in keys


# ---------------------------------------------------------------------- probe

def _provider_returning(message: Message):
    class _P:
        async def chat(self, messages, tools=None, system_prompt="", **kwargs):
            return message

        async def close(self):
            return None

    return _P()


async def test_probe_turns_a_tool_call_into_a_fact(catalog):
    ref = catalog.registry.ref("lmstudio", "qwen3-coder-32b")

    async def build_async(ref):
        return _provider_returning(
            Message(role="assistant", content=[ToolUseBlock(name="probe_ping", arguments={"ok": "yes"}, id="1")])
        )

    catalog.registry.build_async = build_async
    result = await catalog.probe(ref)
    assert result.usable is True
    assert result.ref.caps.probed is True
    assert catalog.probe_result(ref).ref.caps.probed is True


async def test_probe_exposes_a_model_that_only_writes_prose(catalog):
    """Servers advertise tool support they do not deliver; the probe catches it."""
    ref = catalog.registry.ref("lmstudio", "small-model")

    async def build_async(ref):
        return _provider_returning(Message(role="assistant", content=[TextBlock(text="I cannot call tools")]))

    catalog.registry.build_async = build_async
    result = await catalog.probe(ref)
    assert result.ok is True
    assert result.usable is False
    assert result.ref.caps.tools is False
    assert "prose" in result.error


async def test_probe_reports_connection_failures_without_raising(catalog):
    ref = catalog.registry.ref("lmstudio", "gone")

    async def build_async(ref):
        raise ConnectionRefusedError("server not running")

    catalog.registry.build_async = build_async
    result = await catalog.probe(ref)
    assert result.ok is False
    assert "ConnectionRefusedError" in result.error


async def test_probe_respects_its_timeout(catalog):
    ref = catalog.registry.ref("lmstudio", "slow")

    class Slow:
        async def chat(self, messages, tools=None, system_prompt="", **kwargs):
            await asyncio.sleep(5)
            return Message(role="assistant", content="late")

        async def close(self):
            return None

    async def build_async(ref):
        return Slow()

    catalog.registry.build_async = build_async
    result = await catalog.probe(ref, timeout=0.1)
    assert result.ok is False
    assert "timed out" in result.error
