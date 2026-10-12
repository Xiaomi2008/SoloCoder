"""ProviderRegistry: ModelRef in, working provider out.

Endpoints are described once (vendor, wire API, auth mode, base URL, discovery
path, default capabilities). The factory maps *wire API* to an adapter, so a new
OpenAI-compatible server needs no new code — only a new endpoint entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from openagent.core.logging import logger
from openagent.provider.auth.broker import AuthBroker
from openagent.provider.auth.tokens import Credentials
from openagent.provider.model_ref import (
    AuthMode,
    Capabilities,
    CostHint,
    CostKind,
    ModelRef,
    WireAPI,
)


class ProviderDependencyError(RuntimeError):
    """An adapter needs an optional dependency that is not installed.

    Note this is NOT ``openagent.providers.ProviderError``: that is a stream
    *event* describing a failed turn, and raising it would raise a dataclass.
    """


# Adapters whose SDK is an optional install. The import happens inside build(),
# so a missing SDK surfaces here — turn it into one actionable line instead of a
# stack trace in the middle of the app's UI.
OPTIONAL_EXTRAS = {
    WireAPI.OLLAMA_CHAT: "openagent[ollama]",
    WireAPI.GEMINI: "openagent[google]",
}

# Local servers default to optimistic capability claims; the probe downgrades
# them to facts. context_window is the common local default.
_LOCAL_CAPS = Capabilities(tools=True, streaming=True, context_window=32768, max_output_tokens=8192)


@dataclass(frozen=True)
class EndpointSpec:
    provider: str
    label: str
    wire_api: WireAPI
    auth_mode: AuthMode
    base_url: str | None = None
    discover_path: str | None = None
    default_model: str = ""
    default_caps: Capabilities = _LOCAL_CAPS
    local: bool = False
    docs_url: str = ""
    # Extra env var that can point this endpoint at a non-default host.
    host_env: str = ""


LOCAL_ENDPOINTS: tuple[EndpointSpec, ...] = (
    EndpointSpec(
        provider="lmstudio",
        label="LM Studio",
        wire_api=WireAPI.OPENAI_CHAT,
        auth_mode=AuthMode.LOCAL,
        base_url="http://localhost:1234/v1",
        discover_path="/models",
        default_model="qwen3-coder-32b",
        local=True,
        docs_url="https://lmstudio.ai",
        host_env="LMSTUDIO_BASE_URL",
    ),
    EndpointSpec(
        provider="ollama",
        label="Ollama",
        wire_api=WireAPI.OLLAMA_CHAT,
        auth_mode=AuthMode.LOCAL,
        base_url="http://localhost:11434",
        discover_path="/api/tags",
        default_model="qwen3-coder:30b",
        local=True,
        docs_url="https://ollama.com",
        host_env="OLLAMA_HOST",
    ),
    EndpointSpec(
        provider="mlx",
        label="MLX-LM Server",
        wire_api=WireAPI.OPENAI_CHAT,
        auth_mode=AuthMode.LOCAL,
        base_url="http://localhost:8080/v1",
        discover_path="/models",
        default_model="mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit",
        local=True,
        docs_url="https://github.com/ml-explore/mlx-examples",
        host_env="MLX_BASE_URL",
    ),
    EndpointSpec(
        provider="llama_cpp",
        label="llama.cpp server",
        wire_api=WireAPI.OPENAI_CHAT,
        auth_mode=AuthMode.LOCAL,
        base_url="http://localhost:8080/v1",
        discover_path="/models",
        default_model="local",
        local=True,
        host_env="LLAMA_CPP_BASE_URL",
    ),
    EndpointSpec(
        provider="vllm",
        label="vLLM / vLLM-MLX",
        wire_api=WireAPI.OPENAI_CHAT,
        auth_mode=AuthMode.LOCAL,
        base_url="http://localhost:8000/v1",
        discover_path="/models",
        default_model="local",
        local=True,
        host_env="VLLM_BASE_URL",
    ),
    EndpointSpec(
        # vLLM-MLX / Rapid-MLX expose an Anthropic-compatible /v1/messages.
        # Worth its own endpoint: Claude-formatted tool calling is the most
        # reliable dialect for coding agents.
        provider="anthropic_local",
        label="Anthropic-compatible local server",
        wire_api=WireAPI.ANTHROPIC_MESSAGES,
        auth_mode=AuthMode.LOCAL,
        base_url="http://localhost:8000",
        discover_path="/v1/models",
        default_model="local",
        local=True,
        host_env="ANTHROPIC_LOCAL_BASE_URL",
    ),)

CLOUD_ENDPOINTS: tuple[EndpointSpec, ...] = (
    EndpointSpec(
        provider="anthropic",
        label="Anthropic API",
        wire_api=WireAPI.ANTHROPIC_MESSAGES,
        auth_mode=AuthMode.API_KEY,
        base_url="https://api.anthropic.com",
        discover_path="/v1/models",
        default_model="claude-sonnet-4-5",
        default_caps=Capabilities(tools=True, vision=True, reasoning=True, streaming=True, json_mode=True, context_window=200000, max_output_tokens=64000),
        docs_url="https://docs.anthropic.com",
    ),
    EndpointSpec(
        provider="anthropic_oauth",
        label="Claude Pro / Max (unofficial)",
        wire_api=WireAPI.ANTHROPIC_MESSAGES,
        auth_mode=AuthMode.OAUTH_SUBSCRIPTION,
        base_url="https://api.anthropic.com",
        default_model="claude-sonnet-4-5",
        default_caps=Capabilities(tools=True, vision=True, reasoning=True, streaming=True, context_window=200000, max_output_tokens=32000),
        docs_url="https://claude.ai",
    ),
    EndpointSpec(
        provider="openai",
        label="OpenAI API",
        wire_api=WireAPI.OPENAI_CHAT,
        auth_mode=AuthMode.API_KEY,
        base_url="https://api.openai.com/v1",
        discover_path="/models",
        default_model="gpt-4o",
        default_caps=Capabilities(tools=True, vision=True, reasoning=True, streaming=True, json_mode=True, context_window=128000, max_output_tokens=16000),
        docs_url="https://platform.openai.com",
    ),
    EndpointSpec(
        provider="openai_oauth",
        label="ChatGPT Plus / Pro (unofficial)",
        wire_api=WireAPI.OPENAI_RESPONSES,
        auth_mode=AuthMode.OAUTH_SUBSCRIPTION,
        # Subscription JWTs are rejected by api.openai.com; only this backend
        # accepts them, and it speaks the Responses API.
        base_url="https://chatgpt.com/backend-api/codex",
        default_model="gpt-5-codex",
        default_caps=Capabilities(tools=True, vision=True, reasoning=True, streaming=True, context_window=200000, max_output_tokens=32000),
        docs_url="https://chatgpt.com",
    ),
    EndpointSpec(
        provider="gemini",
        label="Google Gemini API",
        wire_api=WireAPI.GEMINI,
        auth_mode=AuthMode.API_KEY,
        base_url=None,
        default_model="gemini-2.0-flash",
        default_caps=Capabilities(tools=True, vision=True, streaming=True, context_window=1000000, max_output_tokens=8192),
        docs_url="https://ai.google.dev",
    ),
)


class ProviderRegistry:
    """Endpoint table + adapter factory."""

    def __init__(
        self,
        broker: AuthBroker | None = None,
        endpoints: list[EndpointSpec] | None = None,
    ) -> None:
        self.broker = broker or AuthBroker()
        table = endpoints if endpoints is not None else [*LOCAL_ENDPOINTS, *CLOUD_ENDPOINTS]
        self._endpoints: dict[str, EndpointSpec] = {e.provider: e for e in table}

    # ------------------------------------------------------------- endpoints

    @property
    def endpoints(self) -> dict[str, EndpointSpec]:
        return dict(self._endpoints)

    def endpoint(self, provider: str) -> EndpointSpec:
        if provider not in self._endpoints:
            raise KeyError(f"Unknown provider '{provider}'. Known: {', '.join(sorted(self._endpoints))}")
        return self._endpoints[provider]

    def local_endpoints(self) -> list[EndpointSpec]:
        return [e for e in self._endpoints.values() if e.local]

    def base_url_for(self, spec: EndpointSpec) -> str | None:
        """Honour a host override env var (``OLLAMA_HOST`` etc.)."""
        if spec.host_env:
            import os

            override = os.environ.get(spec.host_env)
            if override:
                return override.rstrip("/")
        return spec.base_url

    # ---------------------------------------------------------------- refs

    def ref(
        self,
        provider: str,
        model: str | None = None,
        *,
        base_url: str | None = None,
        caps: Capabilities | None = None,
        label: str = "",
        cost: CostHint | None = None,
    ) -> ModelRef:
        """Build a ModelRef from an endpoint's defaults."""
        spec = self.endpoint(provider)
        chosen = model or spec.default_model
        return ModelRef(
            model=chosen,
            provider=provider,
            wire_api=spec.wire_api,
            auth_mode=spec.auth_mode,
            base_url=base_url or self.base_url_for(spec),
            label=label or chosen,
            family=_family_of(chosen),
            caps=caps or spec.default_caps,
            cost=cost or (
                CostHint(CostKind.LOCAL) if spec.local
                else CostHint(CostKind.SUBSCRIPTION)
                if spec.auth_mode is AuthMode.OAUTH_SUBSCRIPTION
                else CostHint(CostKind.PER_TOKEN)
            ),
            is_local=spec.local,
        )

    # -------------------------------------------------------------- factory

    def build(self, ref: ModelRef, credentials: Credentials | None = None) -> Any:
        """Construct a provider for ``ref``. Synchronous: no network here."""
        try:
            return self._build(ref, credentials)
        except ImportError as exc:
            package = OPTIONAL_EXTRAS.get(ref.wire_api)
            if package is None:
                raise
            raise ProviderDependencyError(
                f"The '{ref.wire_api.value}' adapter needs an optional dependency: "
                f"pip install {package}"
            ) from exc

    def _build(self, ref: ModelRef, credentials: Credentials | None = None) -> Any:
        creds = credentials or self.broker.resolve(ref)
        headers = dict(creds.headers)

        if ref.wire_api is WireAPI.OPENAI_CHAT:
            from openagent.provider.openai import OpenAIProvider

            kwargs: dict[str, Any] = {"model": ref.model, "base_url": ref.base_url}
            if creds.api_key:
                kwargs["api_key"] = creds.api_key
            if headers:
                kwargs["default_headers"] = headers
            return OpenAIProvider(**kwargs)

        if ref.wire_api is WireAPI.OPENAI_RESPONSES:
            from openagent.provider.responses import OpenAIResponsesProvider

            token = creds.bearer_token or creds.api_key
            return OpenAIResponsesProvider(
                model=ref.model,
                api_key=token,
                base_url=ref.base_url,
                default_headers=headers or None,
            )

        if ref.wire_api is WireAPI.ANTHROPIC_MESSAGES:
            from openagent.provider.anthropic import AnthropicProvider

            kwargs = {"model": ref.model}
            if ref.base_url:
                kwargs["base_url"] = ref.base_url
            if creds.bearer_token:
                # OAuth Bearer token, not an API key.
                kwargs["auth_token"] = creds.bearer_token
                kwargs["api_key"] = None
            else:
                kwargs["api_key"] = creds.api_key or ("local" if ref.is_local else None)
            if headers:
                kwargs["default_headers"] = headers
            return AnthropicProvider(**kwargs)

        if ref.wire_api is WireAPI.OLLAMA_CHAT:
            from openagent.provider.ollama import OllamaProvider

            kwargs = {"model": ref.model}
            if ref.base_url:
                kwargs["host"] = ref.base_url
            return OllamaProvider(**kwargs)

        if ref.wire_api is WireAPI.GEMINI:
            from openagent.provider.google import GoogleProvider

            return GoogleProvider(model=ref.model, api_key=creds.api_key)

        raise ValueError(f"No adapter for wire API '{ref.wire_api}'")

    async def build_async(self, ref: ModelRef) -> Any:
        """Resolve credentials (refreshing expired subscription tokens) and build."""
        credentials = await self.broker.refresh_if_needed(ref)
        return self.build(ref, credentials)


def _family_of(model: str) -> str:
    lowered = model.lower()
    for family in ("claude", "gpt", "qwen", "llama", "deepseek", "gemini", "mistral", "phi", "glm", "minimax"):
        if family in lowered:
            return family
    return lowered.split("-")[0] if lowered else ""
