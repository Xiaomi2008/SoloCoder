"""Providers own HTTP clients; a long-lived app must be able to close them."""

from __future__ import annotations

import pytest

from openagent.provider.auth.tokens import Credentials
from openagent.provider.base import BaseProvider
from openagent.provider.model_ref import AuthMode
from openagent.provider.openai import OpenAIProvider
from openagent.provider.registry import ProviderRegistry
from openagent.provider.responses import OpenAIResponsesProvider


class Minimal(BaseProvider):
    async def chat(self, messages, tools=None, system_prompt="", **kwargs):
        raise NotImplementedError


async def test_close_awaits_an_async_client_close():
    calls = []

    class Client:
        async def close(self):
            calls.append("closed")

    provider = Minimal(model="m")
    provider._client = Client()
    await provider.close()
    assert calls == ["closed"]


async def test_close_handles_a_sync_client_close():
    calls = []

    class Client:
        def close(self):
            calls.append("closed")

    provider = Minimal(model="m")
    provider._client = Client()
    await provider.close()
    assert calls == ["closed"]


async def test_close_is_a_noop_without_a_client():
    await Minimal(model="m").close()  # must not raise


async def test_close_works_on_the_real_sdk_clients():
    """The adapters that matter: the OpenAI SDK's close is async, Anthropic's too."""
    openai_provider = OpenAIProvider(model="gpt-4o", api_key="sk-x", base_url="http://localhost:1234/v1")
    responses_provider = OpenAIResponsesProvider(model="gpt-5-codex", api_key="jwt", base_url="http://localhost:1235")
    await openai_provider.close()
    await responses_provider.close()


async def test_registry_built_providers_are_closable():
    """The catalog probe closes whatever it builds, for every wire API."""
    from openagent.provider.registry import ProviderDependencyError

    registry = ProviderRegistry()
    local = Credentials(mode=AuthMode.LOCAL)
    built = 0
    for spec in registry.local_endpoints():
        try:
            provider = registry.build(registry.ref(spec.provider), local)
        except ProviderDependencyError as exc:
            assert "optional dependency" in str(exc)
            continue
        await provider.close()
        built += 1
    assert built >= 4, "the OpenAI-compatible adapters must all build"
