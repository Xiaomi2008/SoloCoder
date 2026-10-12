"""ModelRouter: policies, role overrides, and honest failure."""

from __future__ import annotations

import pytest

from openagent.provider.model_ref import (
    Capabilities,
    CostHint,
    CostKind,
    ModelRef,
    WireAPI,
)
from openagent.provider.router import (
    ModelRouter,
    NoCapableModel,
    RoutingPolicy,
    RoutingRules,
)


def make(
    key: str,
    *,
    local: bool = False,
    tools: bool = True,
    vision: bool = False,
    context: int = 128000,
    cost: CostHint | None = None,
    probed: bool = False,
) -> ModelRef:
    provider, model = key.split("/", 1)
    return ModelRef(
        model=model,
        provider=provider,
        wire_api=WireAPI.OPENAI_CHAT,
        label=model,
        is_local=local,
        caps=Capabilities(
            tools=tools, vision=vision, streaming=True,
            context_window=context, probed=probed,
        ),
        cost=cost or (CostHint(CostKind.LOCAL) if local else CostHint(CostKind.PER_TOKEN)),
    )


class FakeCatalog:
    def __init__(self, refs):
        self._refs = {ref.key: ref for ref in refs}

    def all_models(self):
        return list(self._refs.values())

    def get(self, key):
        return self._refs.get(key)

    def probe_result(self, ref):
        return None


@pytest.fixture
def pool() -> list[ModelRef]:
    return [
        make("lmstudio/qwen3-coder-32b", local=True, context=32768),
        make("ollama/llama3.2-vision", local=True, vision=True, context=128000),
        make("mlx/tiny-prose", local=True, tools=False),
        make("anthropic/claude-sonnet-4-5", vision=True, context=200000,
             cost=CostHint(CostKind.PER_TOKEN, 3.0, 15.0)),
        make("gemini/gemini-2.0-flash", vision=True, context=1000000,
             cost=CostHint(CostKind.PER_TOKEN, 0.10, 0.40)),
    ]


def router(pool, policy, default_key="", roles=None) -> ModelRouter:
    r = ModelRouter(FakeCatalog(pool))
    r.rules = RoutingRules(policy=RoutingPolicy(policy), default_key=default_key, roles=dict(roles or {}))
    return r


# ------------------------------------------------------------------- policies

def test_local_first_prefers_the_gpu_you_already_paid_for(pool):
    assert router(pool, "local_first").select().provider in {"lmstudio", "ollama", "mlx"}


def test_cloud_first_prefers_the_cloud(pool):
    assert not router(pool, "cloud_first").select().is_local


def test_local_models_win_cheapest_capable(pool):
    """On Apple Silicon the cheapest capable model usually is the local one:
    marginal cost is hardware already bought, so 0."""
    assert router(pool, "cheapest_capable").select(Capabilities(tools=True)).is_local is True


def test_cheapest_capable_picks_the_cheapest_paid_model(pool):
    cloud_only = [ref for ref in pool if not ref.is_local]
    chosen = router(cloud_only, "cheapest_capable").select(Capabilities(tools=True))
    assert chosen.key == "gemini/gemini-2.0-flash"


def test_privacy_lock_never_leaves_the_machine(pool):
    chosen = router(pool, "privacy_lock").select(Capabilities(vision=True))
    assert chosen.key == "ollama/llama3.2-vision"


def test_privacy_lock_fails_rather_than_leaking(pool):
    """No local vision model: refuse, do not silently phone home."""
    only_cloud = [make("anthropic/claude-sonnet-4-5", vision=True)]
    with pytest.raises(NoCapableModel):
        router(only_cloud, "privacy_lock").select(Capabilities(vision=True))


def test_capability_requirement_excludes_models_that_cannot_do_it(pool):
    chosen = router(pool, "local_first").select(Capabilities(tools=True))
    assert chosen.key != "mlx/tiny-prose"


def test_no_model_can_meet_a_huge_context(pool):
    with pytest.raises(NoCapableModel, match="context"):
        router(pool, "local_first").select(Capabilities(context_window=10_000_000))


def test_failure_message_names_the_missing_capability(pool):
    with pytest.raises(NoCapableModel, match="image input"):
        router([make("mlx/text-only", local=True)], "privacy_lock").select(Capabilities(vision=True))


# ------------------------------------------------------------------- overrides

def test_role_override_wins_over_the_policy(pool):
    r = router(pool, "local_first", roles={"compact": "mlx/tiny-prose"})
    assert r.select(role="compact").key == "mlx/tiny-prose"


def test_role_override_that_cannot_cope_falls_back(pool):
    """A cheap local model for compaction is fine; asking it for vision is not."""
    r = router(pool, "local_first", roles={"main": "mlx/tiny-prose"})
    chosen = r.select(Capabilities(tools=True), role="main")
    assert chosen.key != "mlx/tiny-prose"


def test_default_key_is_honoured_under_fixed(pool):
    r = router(pool, "fixed", default_key="anthropic/claude-sonnet-4-5")
    assert r.select().key == "anthropic/claude-sonnet-4-5"


def test_unknown_role_falls_through_to_the_policy(pool):
    r = router(pool, "cloud_first", roles={"main": "lmstudio/qwen3-coder-32b"})
    assert r.select(role="verify").is_local is False


# ----------------------------------------------------------------- probe facts

def test_a_probed_model_outranks_one_that_only_claims(pool):
    """Facts beat marketing, even when the claim looks better on paper."""
    claims = make("lmstudio/claims-tools", local=True, context=32768, probed=False)
    proved = make("lmstudio/proved-tools", local=True, context=16384, probed=True)
    r = router([claims, proved], "local_first")
    assert r.select(Capabilities(tools=True)).key == "lmstudio/proved-tools"


def test_rules_round_trip_through_config_shapes(pool):
    rules = RoutingRules(
        policy=RoutingPolicy.LOCAL_FIRST,
        default_key="lmstudio/qwen3-coder-32b",
        roles={"compact": "mlx/tiny-prose"},
    )
    assert RoutingRules.from_dict(rules.to_dict()) == rules
