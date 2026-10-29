"""ModelCatalog: what models exist, and what they can *actually* do.

Three sources, in order of trustworthiness:

1. **Probed** — a live round-trip proved it (``caps.probed == True``)
2. **Discovered** — the server listed the model; capability claims are the
   server's, and local servers routinely overclaim tool support
3. **Built-in** — a curated table giving the app sensible defaults offline

The probe is the point: a coding agent that silently picks a model which cannot
emit a tool call is worse than one that refuses to start.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from openagent.core.logging import logger
from openagent.core.types import Message, ToolDef
from openagent.provider.model_ref import AuthMode, Capabilities, CostHint, CostKind, ModelRef, WireAPI
from openagent.provider.registry import ProviderRegistry

PROBE_TOOL = ToolDef(
    name="probe_ping",
    description="Call this tool exactly once to confirm tool calling works.",
    parameters={
        "type": "object",
        "properties": {"ok": {"type": "string", "description": "Always 'yes'"}},
        "required": ["ok"],
    },
)
PROBE_PROMPT = 'Call the "probe_ping" tool with ok="yes". Do not answer in prose.'


@dataclass
class ProbeResult:
    ref: ModelRef
    ok: bool
    tools_ok: bool = False
    latency_ms: float = 0.0
    error: str = ""

    @property
    def usable(self) -> bool:
        return self.ok and self.tools_ok


@dataclass
class DiscoveredModel:
    model: str
    label: str = ""
    caps: Capabilities = field(default_factory=Capabilities)


class ModelCatalog:
    def __init__(self, registry: ProviderRegistry | None = None) -> None:
        self.registry = registry or ProviderRegistry()
        self._discovered: dict[str, list[ModelRef]] = {}
        self._probed: dict[str, ProbeResult] = {}
        # provider -> one-line reason, filled by discover(). "No models" is not a
        # diagnosis; a server that wants a key must say so.
        self.discovery_errors: dict[str, str] = {}

    # ------------------------------------------------------------- built-ins

    def builtin(self) -> list[ModelRef]:
        """Curated defaults so the picker is populated before any probing."""
        refs: list[ModelRef] = []
        for provider, model, caps, cost in _BUILTINS:
            try:
                refs.append(
                    self.registry.ref(
                        provider,
                        model,
                        caps=caps,
                        cost=cost,
                        label=model.split("/")[-1],
                    )
                )
            except KeyError:  # endpoint not in this registry
                continue
        return refs

    def all_models(self) -> list[ModelRef]:
        """Built-ins plus everything discovered, deduped by key, probes applied."""
        by_key: dict[str, ModelRef] = {}
        for ref in self.builtin():
            by_key[ref.key] = ref
        for refs in self._discovered.values():
            for ref in refs:
                by_key[ref.key] = ref
        for key, result in self._probed.items():
            if key in by_key:
                by_key[key] = result.ref
        return sorted(by_key.values(), key=lambda r: (not r.is_local, r.provider, r.model))

    def get(self, key: str) -> ModelRef | None:
        return self._by_key().get(key)

    def _by_key(self) -> dict[str, ModelRef]:
        return {ref.key: ref for ref in self.all_models()}

    def probe_result(self, ref: ModelRef) -> ProbeResult | None:
        return self._probed.get(ref.key)

    # ------------------------------------------------------------- discovery

    async def discover(
        self, provider: str | None = None, *, timeout: float = 2.0
    ) -> dict[str, list[ModelRef]]:
        """Query running servers for their model lists.

        Never raises: an unreachable server is the normal case (nothing is
        running until the user starts one), so it reports an empty list.
        """
        specs = (
            [self.registry.endpoint(provider)]
            if provider
            else [*self.registry.local_endpoints(), *self._signed_in_subscriptions()]
        )
        self.discovery_errors = {}

        async def one(spec: Any) -> tuple[str, list[ModelRef]]:
            base = self.registry.base_url_for(spec)
            if not base or not spec.discover_path:
                return spec.provider, []
            url = f"{base.rstrip('/')}{spec.discover_path}"
            # A cloud backend is not a server on this machine; 2s is too tight.
            wait = timeout if spec.local else max(timeout, 10.0)
            try:
                if spec.auth_mode is AuthMode.OAUTH_SUBSCRIPTION:
                    headers = await self._subscription_headers(spec.provider)
                else:
                    headers = self._discovery_headers(spec.provider)
                async with httpx.AsyncClient(timeout=wait) as client:
                    response = await client.get(url, headers=headers)
                    response.raise_for_status()
                    payload = response.json()
            except Exception as exc:
                # Silent failure here costs an hour: "no models" is a very
                # different problem when the server wants a key.
                self.discovery_errors[spec.provider] = _discovery_error(spec, url, exc, wait)
                logger.debug(f"Discovery failed for {spec.provider} at {url}: {exc}")
                return spec.provider, []

            refs = [
                self.registry.ref(
                    spec.provider,
                    item.model,
                    caps=item.caps,
                    label=item.label,
                )
                for item in _parse_listing(
                    spec.wire_api, payload, spec.default_caps.context_window, spec.default_caps
                )
            ]
            return spec.provider, refs

        results = await asyncio.gather(*(one(spec) for spec in specs))
        for provider_key, refs in results:
            self._discovered[provider_key] = refs
        return {k: v for k, v in results if v}

    def _signed_in_subscriptions(self) -> list[Any]:
        """Subscription endpoints with a stored sign-in. Without one there is
        nothing to list, and asking would only add an auth error per launch."""
        out = []
        for spec in self.registry.endpoints.values():
            if spec.auth_mode is not AuthMode.OAUTH_SUBSCRIPTION or not spec.discover_path:
                continue
            try:
                creds = self.registry.broker.load_credentials(spec.provider)
            except Exception:
                continue
            if creds is not None and creds.tokens is not None:
                out.append(spec)
        return out

    async def _subscription_headers(self, provider: str) -> dict[str, str]:
        """Bearer plus the vendor headers, refreshing an expired token first."""
        creds = await self.registry.broker.refresh_if_needed(self.registry.ref(provider))
        return {**creds.headers, "Authorization": f"Bearer {creds.bearer_token}"}

    def _discovery_headers(self, provider: str) -> dict[str, str]:
        """A key-protected server is undiscoverable without its stored key."""
        try:
            creds = self.registry.broker.load_credentials(provider)
        except Exception as exc:  # a broken credential must not break discovery
            logger.debug(f"Discovery credentials for {provider} unavailable: {exc}")
            return {}
        token = getattr(creds, "bearer_token", None) or getattr(creds, "api_key", None)
        return {"Authorization": f"Bearer {token}"} if token else {}

    # ----------------------------------------------------------------- probe

    async def probe(self, ref: ModelRef, *, timeout: float = 60.0) -> ProbeResult:
        """One tiny tool-calling round-trip. Turns claims into facts."""
        started = time.perf_counter()
        provider = None
        try:
            provider = await self.registry.build_async(ref)
            response = await asyncio.wait_for(
                provider.chat([Message(role="user", content=PROBE_PROMPT)], tools=[PROBE_TOOL]),
                timeout=timeout,
            )
            latency = (time.perf_counter() - started) * 1000
            tools_ok = response.has_tool_calls
            probed = ref.with_caps(
                tools=tools_ok,
                streaming=ref.caps.streaming,
                context_window=ref.caps.context_window,
                probed=True,
            )
            result = ProbeResult(
                ref=probed,
                ok=True,
                tools_ok=tools_ok,
                latency_ms=latency,
                error="" if tools_ok else "model answered in prose; no tool call emitted",
            )
        except asyncio.TimeoutError:
            result = ProbeResult(
                ref=ref, ok=False, latency_ms=(time.perf_counter() - started) * 1000,
                error=f"probe timed out after {timeout:.0f}s",
            )
        except Exception as exc:
            result = ProbeResult(
                ref=ref, ok=False, latency_ms=(time.perf_counter() - started) * 1000,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            if provider is not None:
                try:
                    await provider.close()
                except Exception:
                    pass

        self._probed[ref.key] = result
        self._discovered.setdefault(ref.provider, [])
        if not any(r.key == ref.key for r in self._discovered[ref.provider]):
            self._discovered[ref.provider].append(result.ref)
        return result

    def clear(self) -> None:
        self._discovered.clear()
        self._probed.clear()


def _discovery_error(spec: Any, url: str, exc: Exception, timeout: float) -> str:
    """Turn a discovery failure into one line the user can act on."""
    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
    if status in (401, 403):
        return f"{url} needs an API key — store it with: auth login {spec.provider} --api-key <key>"
    if status == 404:
        return f"{url} returned 404 — no model list at that path"
    if isinstance(exc, httpx.TimeoutException):
        return f"{url} did not answer within {timeout:g}s"
    return f"{urlsplit(url).netloc} is not answering — is the server running?"


def _parse_listing(
    wire_api: WireAPI,
    payload: Any,
    default_context: int = 32768,
    default_caps: Capabilities | None = None,
) -> list[DiscoveredModel]:
    """Normalize the three listing dialects we speak.

    ``default_context`` is the endpoint's configured window, used when the
    server does not report one (most OpenAI-compatible servers don't).
    """
    out: list[DiscoveredModel] = []

    if wire_api is WireAPI.OLLAMA_CHAT:
        for entry in payload.get("models", []) if isinstance(payload, dict) else []:
            name = entry.get("name") or entry.get("model")
            if not name:
                continue
            details = entry.get("details") or {}
            declared = set(entry.get("capabilities") or [])
            context = entry.get("context_window") or details.get("context_length") or 0
            params = details.get("parameter_count") or 0
            out.append(
                DiscoveredModel(
                    model=name,
                    label=name,
                    caps=Capabilities(
                        tools="tools" in declared,
                        vision="vision" in declared,
                        streaming=True,
                        context_window=int(context) if context else default_context,
                        max_output_tokens=8192,
                    ),
                )
            )
        return out

    if wire_api is WireAPI.OPENAI_RESPONSES and isinstance(payload, dict) and isinstance(payload.get("models"), list):
        # The ChatGPT subscription backend: Codex's own model list. "hide" rows
        # are internal (review models, reserves); the picker shows what Codex does.
        for entry in payload["models"]:
            slug = entry.get("slug") if isinstance(entry, dict) else None
            if not slug or entry.get("visibility", "list") != "list":
                continue
            context = entry.get("context_window") or 0
            out.append(
                DiscoveredModel(
                    model=slug,
                    label=entry.get("display_name") or slug,
                    caps=Capabilities(
                        tools=True,
                        vision="image" in (entry.get("input_modalities") or []),
                        reasoning=bool(entry.get("supported_reasoning_levels")),
                        streaming=True,
                        context_window=int(context) if context else default_context,
                        max_output_tokens=default_caps.max_output_tokens if default_caps else 8192,
                    ),
                )
            )
        return out

    # OpenAI-compatible (LM Studio, MLX, llama.cpp, vLLM) and Anthropic-compat
    # servers that expose /v1/models.
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        for entry in data:
            model_id = entry.get("id") or entry.get("name")
            if not model_id:
                continue
            meta = entry.get("meta") or entry.get("details") or {}
            context = meta.get("context_length") or meta.get("max_context") or 0
            out.append(
                DiscoveredModel(
                    model=model_id,
                    label=str(model_id).split("/")[-1],
                    caps=Capabilities(
                        tools=True,
                        streaming=True,
                        context_window=int(context) if context else default_context,
                        max_output_tokens=8192,
                    ),
                )
            )
    return out


# (provider, model, caps, cost) — costs are published list prices per million tokens.
def _caps(**kw: Any) -> Capabilities:
    return Capabilities(**kw)


_BUILTINS: tuple[tuple[str, str, Capabilities, CostHint], ...] = (
    # Hand-curated, offline, and it goes stale: nothing here is fetched, so a
    # model that ships tomorrow does not appear until this table is edited. It
    # exists so the picker is not empty on first launch with no server running,
    # and every row is a claim — `models probe` is what turns it into a fact, and
    # discovery from a running server replaces the row wholesale. Prices are
    # deliberately left unset where they are not known: the CLI renders an
    # unknown price as the cost kind, not a guess.
    (
        "anthropic", "claude-sonnet-5-5",
        _caps(tools=True, vision=True, reasoning=True, streaming=True, json_mode=True, context_window=200000, max_output_tokens=64000),
        CostHint(CostKind.PER_TOKEN),
    ),
    (
        "anthropic", "claude-opus-5-5",
        _caps(tools=True, vision=True, reasoning=True, streaming=True, json_mode=True, context_window=200000, max_output_tokens=32000),
        CostHint(CostKind.PER_TOKEN),
    ),
    (
        "anthropic", "claude-haiku-4-5",
        _caps(tools=True, vision=True, streaming=True, json_mode=True, context_window=200000, max_output_tokens=8192),
        CostHint(CostKind.PER_TOKEN),
    ),
    (
        "anthropic_oauth", "claude-opus-5-5",
        _caps(tools=True, vision=True, reasoning=True, streaming=True, context_window=200000, max_output_tokens=32000),
        CostHint(CostKind.SUBSCRIPTION),
    ),
    (
        "openai", "gpt-4o",
        _caps(tools=True, vision=True, streaming=True, json_mode=True, context_window=128000, max_output_tokens=16000),
        CostHint(CostKind.PER_TOKEN, 2.5, 10.0),
    ),
    (
        "openai_oauth", "gpt-6.1-sol",
        _caps(tools=True, vision=True, reasoning=True, streaming=True, context_window=500000, max_output_tokens=32000),
        CostHint(CostKind.SUBSCRIPTION),
    ),
    # The subscription backend answers an id it does not serve with "not
    # supported when using Codex with a ChatGPT account". Discovery
    # (`/models?client_version=...`) lists the ids it does serve; this row is
    # only the offline default.
    (
        "gemini", "gemini-2.0-flash",
        _caps(tools=True, vision=True, streaming=True, context_window=1000000, max_output_tokens=8192),
        CostHint(CostKind.PER_TOKEN, 0.10, 0.40),
    ),
    # Local defaults: capability claims unprobed, cost is hardware only.
    (
        "lmstudio", "qwen3-coder-32b",
        _caps(tools=True, streaming=True, context_window=32768, max_output_tokens=8192),
        CostHint(CostKind.LOCAL),
    ),
    (
        "ollama", "qwen3-coder:30b",
        _caps(tools=True, streaming=True, context_window=32768, max_output_tokens=8192),
        CostHint(CostKind.LOCAL),
    ),
    (
        "mlx", "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit",
        _caps(tools=True, streaming=True, context_window=32768, max_output_tokens=8192),
        CostHint(CostKind.LOCAL),
    ),
)
