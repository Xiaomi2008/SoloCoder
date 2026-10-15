"""ProviderRegistry: endpoint table + the wire-API -> adapter factory."""

from __future__ import annotations

import pytest

from openagent.provider.anthropic import AnthropicProvider
from openagent.provider.auth.tokens import Credentials, TokenSet
from openagent.provider.model_ref import AuthMode, CostKind, ModelRef, WireAPI
from openagent.provider.openai import OpenAIProvider
from openagent.provider.registry import ProviderRegistry
from openagent.provider.responses import OpenAIResponsesProvider


@pytest.fixture
def registry() -> ProviderRegistry:
    return ProviderRegistry()


def test_all_target_endpoints_are_registered(registry):
    expected = {
        "lmstudio", "ollama", "mlx", "llama_cpp", "vllm", "anthropic_local",
        "anthropic", "anthropic_oauth", "openai", "openai_oauth", "gemini",
    }
    assert expected <= set(registry.endpoints)


def test_local_endpoints_are_marked_local(registry):
    locals_ = {e.provider for e in registry.local_endpoints()}
    assert {"lmstudio", "ollama", "mlx", "llama_cpp", "vllm", "anthropic_local"} == locals_


def test_ref_inherits_endpoint_defaults(registry):
    ref = registry.ref("lmstudio")
    assert ref.auth_mode is AuthMode.LOCAL
    assert ref.wire_api is WireAPI.OPENAI_CHAT
    assert ref.is_local is True
    assert ref.cost.kind is CostKind.LOCAL
    assert ref.base_url.endswith("/v1")


def test_subscription_endpoint_uses_the_backend_that_accepts_its_token(registry):
    """api.openai.com rejects ChatGPT subscription JWTs; only the Codex
    Responses backend accepts them, so the endpoint must point there."""
    ref = registry.ref("openai_oauth")
    assert ref.wire_api is WireAPI.OPENAI_RESPONSES
    assert ref.base_url == "https://chatgpt.com/backend-api/codex"
    assert ref.auth_mode is AuthMode.OAUTH_SUBSCRIPTION
    assert ref.cost.kind is CostKind.SUBSCRIPTION


def test_anthropic_local_uses_the_anthropic_dialect(registry):
    """vLLM-MLX / Rapid-MLX expose /v1/messages; Claude-formatted tool calling
    is the reliable dialect for agents, so it gets its own endpoint."""
    assert registry.endpoint("anthropic_local").wire_api is WireAPI.ANTHROPIC_MESSAGES


def test_unknown_provider_is_actionable(registry):
    with pytest.raises(KeyError, match="Unknown provider"):
        registry.endpoint("nope")


def test_host_env_override(registry, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://10.0.0.5:11434")
    assert registry.base_url_for(registry.endpoint("ollama")) == "http://10.0.0.5:11434"


def test_build_local_openai_chat(registry):
    provider = registry.build(registry.ref("lmstudio"), Credentials(mode=AuthMode.LOCAL))
    assert isinstance(provider, OpenAIProvider)
    assert provider.base_url == "http://localhost:1234/v1"


def test_build_api_key_anthropic(registry):
    ref = registry.ref("anthropic", "claude-sonnet-4-5")
    provider = registry.build(ref, Credentials(mode=AuthMode.API_KEY, api_key="sk-test"))
    assert isinstance(provider, AnthropicProvider)
    assert provider._client.api_key == "sk-test"


def test_build_oauth_anthropic_sends_bearer_and_beta_header(registry):
    """OAuth tokens need `Authorization: Bearer` plus anthropic-beta, not
    `x-api-key` — the Messages API 401s without the beta flag."""
    ref = registry.ref("anthropic_oauth")
    creds = Credentials(
        mode=AuthMode.OAUTH_SUBSCRIPTION,
        tokens=TokenSet(access_token="oauth-tok", account_id="acct_1"),
        headers={"anthropic-beta": "oauth-2025-04-20", "anthropic-account-id": "acct_1"},
    )
    provider = registry.build(ref, creds)
    client = provider._client
    assert client.auth_token == "oauth-tok"
    assert client.api_key is None
    assert client._custom_headers["anthropic-beta"] == "oauth-2025-04-20"
    assert client._custom_headers["anthropic-account-id"] == "acct_1"


def test_build_subscription_openai_uses_responses_adapter(registry):
    ref = registry.ref("openai_oauth")
    creds = Credentials(
        mode=AuthMode.OAUTH_SUBSCRIPTION,
        tokens=TokenSet(access_token="jwt", account_id="acct_9"),
        headers={"OpenAI-Organization": "acct_9"},
    )
    provider = registry.build(ref, creds)
    assert isinstance(provider, OpenAIResponsesProvider)
    assert provider.base_url == "https://chatgpt.com/backend-api/codex"
    assert provider._client._custom_headers["OpenAI-Organization"] == "acct_9"


def test_local_anthropic_server_gets_a_placeholder_key(registry):
    """The SDK refuses to construct without any key; local servers ignore it."""
    provider = registry.build(registry.ref("anthropic_local"), Credentials(mode=AuthMode.LOCAL))
    assert provider._client.api_key == "local"


def test_optional_extras_report_their_install_hint(registry):
    """ollama / google-genai are optional extras. A missing one must surface as one
    actionable line naming the package, not a stack trace in the app's UI."""
    import importlib.util

    from openagent.provider.registry import ProviderDependencyError

    if importlib.util.find_spec("ollama") is not None:
        pytest.skip("ollama SDK is installed")
    with pytest.raises(ProviderDependencyError, match=r"pip install openagent\[ollama\]"):
        registry.build(registry.ref("ollama"), Credentials(mode=AuthMode.LOCAL))

    if importlib.util.find_spec("google.genai") is not None:
        pytest.skip("google-genai is installed")
    with pytest.raises(ProviderDependencyError, match=r"pip install openagent\[google\]"):
        registry.build(registry.ref("gemini"), Credentials(mode=AuthMode.API_KEY, api_key="k"))


def test_discovery_urls_never_double_the_v1(registry):
    """OpenAI-compatible base URLs already end in /v1, so the discover path is
    relative to it. A '/v1' in the path here would 404 against every server."""
    for spec in registry.local_endpoints():
        base = registry.base_url_for(spec)
        url = f"{base.rstrip('/')}{spec.discover_path}"
        assert url.count("/v1") <= 1, f"{spec.provider} discovers at {url}"


def test_family_detection(registry):
    assert registry.ref("anthropic", "claude-sonnet-4-5").family == "claude"
    assert registry.ref("mlx", "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit").family == "qwen"
