"""ModelRef is the address every layer of the app uses — its shape is a contract."""

from __future__ import annotations

import pytest

from openagent.provider.model_ref import (
    AuthMode,
    Capabilities,
    CostHint,
    CostKind,
    ModelRef,
    WireAPI,
)


def test_wire_api_values_are_stable():
    # The Swift client decodes these strings; changing one is a protocol break.
    assert str(WireAPI.OPENAI_CHAT) == "openai-chat"
    assert str(WireAPI.OPENAI_RESPONSES) == "openai-responses"
    assert str(WireAPI.ANTHROPIC_MESSAGES) == "anthropic-messages"
    assert str(WireAPI.OLLAMA_CHAT) == "ollama-chat"
    assert str(WireAPI.GEMINI) == "gemini"


def test_auth_mode_values_are_stable():
    assert str(AuthMode.LOCAL) == "local"
    assert str(AuthMode.API_KEY) == "api_key"
    assert str(AuthMode.OAUTH_SUBSCRIPTION) == "oauth_subscription"


def test_key_and_display():
    ref = ModelRef(
        model="qwen3-coder:30b",
        provider="ollama",
        wire_api=WireAPI.OLLAMA_CHAT,
        auth_mode=AuthMode.LOCAL,
        is_local=True,
    )
    assert ref.key == "ollama/qwen3-coder:30b"
    assert ref.display.endswith("(local)")


def test_round_trip_through_dict():
    ref = ModelRef(
        model="claude-sonnet-4-5",
        provider="anthropic_oauth",
        wire_api=WireAPI.ANTHROPIC_MESSAGES,
        auth_mode=AuthMode.OAUTH_SUBSCRIPTION,
        base_url="https://api.anthropic.com",
        label="Claude Sonnet 4.5",
        family="claude",
        caps=Capabilities(tools=True, vision=True, context_window=200000, max_output_tokens=32000, probed=True),
        cost=CostHint(CostKind.SUBSCRIPTION),
    )
    restored = ModelRef.from_dict(ref.to_dict())
    assert restored == ref


def test_caps_supports_is_a_requirement_check_not_equality():
    capable = Capabilities(tools=True, vision=True, context_window=200000)
    assert capable.supports(Capabilities(tools=True))
    assert capable.supports(Capabilities())
    assert not capable.supports(Capabilities(vision=True, context_window=1_000_000))
    assert not Capabilities(tools=False).supports(Capabilities(tools=True))


def test_local_models_have_zero_marginal_cost():
    assert CostHint(CostKind.LOCAL).marginal_cost_per_turn == 0.0
    assert CostHint(CostKind.SUBSCRIPTION).marginal_cost_per_turn == 0.0


def test_per_token_cost_uses_published_prices():
    cost = CostHint(CostKind.PER_TOKEN, input_per_mtok=3.0, output_per_mtok=15.0)
    # 8k in / 1k out per turn: 8000/1e6*3 + 1000/1e6*15 = 0.039
    assert cost.marginal_cost_per_turn == pytest.approx(0.039)


def test_with_caps_leaves_the_original_untouched():
    ref = ModelRef(model="m", provider="p", wire_api=WireAPI.OPENAI_CHAT)
    probed = ref.with_caps(tools=True, probed=True)
    assert ref.caps.tools is False
    assert probed.caps.tools is True and probed.caps.probed is True
