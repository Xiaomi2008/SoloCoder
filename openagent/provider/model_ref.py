"""Model addressing: one type that describes how to reach *any* model.

A ``ModelRef`` is the single address the whole app uses — local MLX server,
LM Studio, Ollama, an API key, or a ChatGPT/Claude subscription. The provider
layer is derived from it, never configured ad hoc.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class AuthMode(StrEnum):
    LOCAL = "local"                          # loopback server, no credential
    API_KEY = "api_key"                      # officially supported cloud path
    OAUTH_SUBSCRIPTION = "oauth_subscription"  # ChatGPT Plus/Pro, Claude Pro/Max


class WireAPI(StrEnum):
    """The HTTP dialect a server speaks.

    Adapters are keyed by wire API, not by vendor: LM Studio, llama.cpp, MLX and
    vLLM all speak ``openai-chat``, and the newer MLX servers speak
    ``anthropic-messages`` too — which matters because Claude-formatted tool
    calling is markedly more reliable for coding agents.
    """

    OPENAI_CHAT = "openai-chat"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    OLLAMA_CHAT = "ollama-chat"
    GEMINI = "gemini"


class CostKind(StrEnum):
    LOCAL = "local"              # hardware only, marginal cost ~0
    SUBSCRIPTION = "subscription"  # flat plan, rate-limited
    PER_TOKEN = "per_token"
    FREE = "free"


@dataclass(frozen=True)
class Capabilities:
    """What a model can do. ``probed`` distinguishes fact from marketing.

    Local servers routinely advertise tool support they do not reliably deliver,
    so anything not ``probed`` is a declaration to be treated as optimistic.
    """

    tools: bool = False
    vision: bool = False
    reasoning: bool = False
    streaming: bool = True
    json_mode: bool = False
    context_window: int = 8192
    max_output_tokens: int = 4096
    probed: bool = False

    def supports(self, other: Capabilities) -> bool:
        """True when this model meets every capability asked for."""
        return (
            (not other.tools or self.tools)
            and (not other.vision or self.vision)
            and (not other.reasoning or self.reasoning)
            and (not other.streaming or self.streaming)
            and (not other.json_mode or self.json_mode)
            and self.context_window >= other.context_window
        )


@dataclass(frozen=True)
class CostHint:
    kind: CostKind = CostKind.LOCAL
    input_per_mtok: float | None = None
    output_per_mtok: float | None = None

    @property
    def marginal_cost_per_turn(self) -> float:
        """Rough per-turn cost used by the cheapest-capable routing policy.

        Local and subscription models score 0: the router's job there is
        capability and latency, not dollars.
        """
        if self.kind in (CostKind.LOCAL, CostKind.SUBSCRIPTION, CostKind.FREE):
            return 0.0
        if self.input_per_mtok is None or self.output_per_mtok is None:
            return 0.0
        # Assume a typical agentic turn: 8k in, 1k out.
        return (8_000 / 1e6) * self.input_per_mtok + (1_000 / 1e6) * self.output_per_mtok


@dataclass(frozen=True)
class ModelRef:
    """Address of a model: vendor, wire API, credential mode, and capabilities."""

    model: str                      # slug sent on the wire
    provider: str                   # vendor key, e.g. "lmstudio", "anthropic"
    wire_api: WireAPI
    auth_mode: AuthMode = AuthMode.LOCAL
    base_url: str | None = None     # None -> vendor default
    label: str = ""                 # human name for pickers
    family: str = ""                # e.g. "claude", "qwen", "gpt"
    caps: Capabilities = field(default_factory=Capabilities)
    cost: CostHint = field(default_factory=CostHint)
    is_local: bool = False
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def key(self) -> str:
        """Stable identity for settings, history, and routing tables."""
        return f"{self.provider}/{self.model}"

    @property
    def display(self) -> str:
        base = self.label or self.model
        if self.is_local:
            return f"{base} (local)"
        return f"{base} [{self.provider}]"

    def with_caps(self, **updates: Any) -> ModelRef:
        return replace(self, caps=replace(self.caps, **updates))

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "wire_api": str(self.wire_api),
            "auth_mode": str(self.auth_mode),
            "base_url": self.base_url,
            "label": self.label,
            "family": self.family,
            "is_local": self.is_local,
            "caps": {
                "tools": self.caps.tools,
                "vision": self.caps.vision,
                "reasoning": self.caps.reasoning,
                "streaming": self.caps.streaming,
                "json_mode": self.caps.json_mode,
                "context_window": self.caps.context_window,
                "max_output_tokens": self.caps.max_output_tokens,
                "probed": self.caps.probed,
            },
            "cost": {
                "kind": str(self.cost.kind),
                "input_per_mtok": self.cost.input_per_mtok,
                "output_per_mtok": self.cost.output_per_mtok,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelRef:
        caps = data.get("caps") or {}
        cost = data.get("cost") or {}
        return cls(
            model=data["model"],
            provider=data["provider"],
            wire_api=WireAPI(data["wire_api"]),
            auth_mode=AuthMode(data.get("auth_mode", AuthMode.LOCAL)),
            base_url=data.get("base_url"),
            label=data.get("label", ""),
            family=data.get("family", ""),
            is_local=bool(data.get("is_local", False)),
            caps=Capabilities(
                tools=bool(caps.get("tools", False)),
                vision=bool(caps.get("vision", False)),
                reasoning=bool(caps.get("reasoning", False)),
                streaming=bool(caps.get("streaming", True)),
                json_mode=bool(caps.get("json_mode", False)),
                context_window=int(caps.get("context_window", 8192)),
                max_output_tokens=int(caps.get("max_output_tokens", 4096)),
                probed=bool(caps.get("probed", False)),
            ),
            cost=CostHint(
                kind=CostKind(cost.get("kind", CostKind.LOCAL)),
                input_per_mtok=cost.get("input_per_mtok"),
                output_per_mtok=cost.get("output_per_mtok"),
            ),
        )
