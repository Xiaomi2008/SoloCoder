"""CLI surface for Phase 1: ``auth`` and ``models``.

Kept out of ``cli_coder.py`` so the same handlers back the future ``agent``
entry point and the service layer.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
from typing import Any

from openagent.provider.auth.broker import API_KEY_PROVIDERS, AuthBroker
from openagent.provider.auth.flow import OAuthError
from openagent.provider.auth.spec import ANTHROPIC_OAUTH, OPENAI_OAUTH, all_specs
from openagent.provider.auth.tokens import AuthRequired, AuthStatus
from openagent.provider.catalog import ModelCatalog
from openagent.provider.model_ref import AuthMode, ModelRef
from openagent.provider.registry import (
    BUILTIN_PROVIDERS,
    WIRE_APIS_BY_NAME,
    ProviderRegistry,
    add_endpoint_config,
    remove_endpoint_config,
)
from openagent.provider.router import ModelRouter, RoutingPolicy

# Friendly aliases so `auth login claude` does the obvious thing.
_PROVIDER_ALIASES = {
    "claude": ANTHROPIC_OAUTH,
    "anthropic": "anthropic",
    "anthropic-oauth": ANTHROPIC_OAUTH,
    "chatgpt": OPENAI_OAUTH,
    "codex": OPENAI_OAUTH,
    "openai": "openai",
    "openai-oauth": OPENAI_OAUTH,
    "gemini": "gemini",
}


def _provider_key(raw: str) -> str:
    return _PROVIDER_ALIASES.get(raw.replace("_", "-"), raw)


def _is_oauth(provider: str) -> bool:
    return provider in all_specs()


# --------------------------------------------------------------------- wiring

def add_subcommands(subparsers: Any) -> None:
    """Register ``auth`` and ``models`` on an existing subparser object."""

    auth = subparsers.add_parser("auth", help="Manage credentials (API keys, subscription sign-in)")
    auth_sub = auth.add_subparsers(dest="auth_action", required=True)

    auth_sub.add_parser("status", help="Show configured credentials")

    login = auth_sub.add_parser("login", help="Sign in or store an API key")
    login.add_argument("provider", help="anthropic | openai | gemini | claude | chatgpt | codex")
    login.add_argument("--api-key", dest="api_key", help="API key (prompted for if omitted)")
    login.add_argument("--no-browser", action="store_true", help="Print the sign-in URL only")

    logout = auth_sub.add_parser("logout", help="Remove stored credentials")
    logout.add_argument("provider")

    models = subparsers.add_parser("models", help="List, discover, probe, and select models")
    models_sub = models.add_subparsers(dest="models_action", required=True)

    list_models = models_sub.add_parser("list", help="List known models")
    list_models.add_argument("--discover", action="store_true", help="Query running local servers")

    probe = models_sub.add_parser("probe", help="Verify a model can call tools")
    probe.add_argument("key", help="provider/model, e.g. lmstudio/qwen3-coder-32b")

    use = models_sub.add_parser("use", help="Set the default model, or a model for one role")
    use.add_argument("key", help="provider/model")
    use.add_argument("--role", choices=("main", "subagent", "compact", "title", "verify"))
    use.add_argument(
        "--policy",
        choices=tuple(str(p) for p in RoutingPolicy),
        help="Routing policy to use from now on",
    )

    add_ep = models_sub.add_parser(
        "add-endpoint",
        help="Register your own model server (LM Studio, llama.cpp, LiteLLM, a NAS box…)",
    )
    add_ep.add_argument("name", help="short lowercase id, e.g. nas — becomes the 'nas/model' prefix")
    add_ep.add_argument("--base-url", required=True, help="e.g. http://192.168.4.34:8080/v1")
    add_ep.add_argument(
        "--wire-api",
        choices=tuple(sorted(WIRE_APIS_BY_NAME)),
        default="openai-chat",
        help="dialect the server speaks (default: openai-chat)",
    )
    add_ep.add_argument("--label", help="display name")
    add_ep.add_argument("--default-model", dest="default_model", help="model id to offer first")
    add_ep.add_argument(
        "--no-key",
        dest="requires_key",
        action="store_false",
        default=True,
        help="server needs no API key",
    )
    add_ep.add_argument(
        "--local",
        action="store_true",
        help="inference stays on your network — lets privacy_lock use it",
    )
    add_ep.add_argument("--context-window", dest="context_window", type=int, default=32768)

    remove_ep = models_sub.add_parser("remove-endpoint", help="Forget a custom model server")
    remove_ep.add_argument("name")


# ---------------------------------------------------------------------- auth

def handle_auth(args: argparse.Namespace) -> int:
    """Sync entry point for a plain CLI. Runs its own event loop."""
    return _run(handle_auth_async(args))


async def handle_auth_async(args: argparse.Namespace) -> int:
    broker = AuthBroker()

    if args.auth_action == "status":
        return _print_status(broker, ProviderRegistry())

    provider = _provider_key(args.provider)

    if args.auth_action == "logout":
        removed = broker.delete_credentials(provider)
        print(f"Removed credentials for {provider}" if removed else f"No stored credentials for {provider}")
        return 0

    if args.auth_action == "login":
        if _is_oauth(provider):
            return await _login_oauth(broker, provider, open_browser=not args.no_browser)
        return _login_api_key(broker, provider, args.api_key)

    return 2


def _print_status(broker: AuthBroker, registry: ProviderRegistry | None = None) -> int:
    rows = broker.status()
    print("\nAPI keys (officially supported)\n")
    for row in rows:
        if row.mode is not AuthMode.API_KEY:
            continue
        mark = "✓" if row.configured else "✗"
        where = f" ({row.source})" if row.configured else ""
        print(f"  {mark} {row.label:34} {row.provider}{'  ' + where if where else '  not set'}")

    print("\nSubscription sign-in (unofficial, at your own risk)\n")
    for row in rows:
        if row.mode is not AuthMode.OAUTH_SUBSCRIPTION:
            continue
        if row.configured:
            bits = [p for p in (row.account, row.plan) if p]
            suffix = f"  {' · '.join(bits)}" if bits else ""
            expiry = "  (token refreshes automatically)" if not row.needs_refresh else "  (needs refresh)"
            print(f"  ✓ {row.label:34} signed in{suffix}{expiry}")
        else:
            print(f"  ✗ {row.label:34} not signed in")

    registry = registry or ProviderRegistry()
    custom = [e for e in registry.endpoints.values() if e.provider not in BUILTIN_PROVIDERS]
    if custom:
        print("\nYour model servers\n")
        for e in custom:
            creds = broker.load_credentials(e.provider)
            has_key = bool(getattr(creds, "api_key", None) or getattr(creds, "bearer_token", None))
            if has_key:
                mark, where = "✓", "key set"
            elif e.auth_mode is AuthMode.LOCAL:
                mark, where = "✓", "no key needed"
            else:
                mark, where = "✗", "no key yet"
            scope = "local network" if e.local else "remote"
            print(f"  {mark} {e.label:20} {e.provider:12} {e.base_url}  {where}, {scope}")

    print()
    return 0


def _login_api_key(broker: AuthBroker, provider: str, api_key: str | None) -> int:
    spec = API_KEY_PROVIDERS.get(provider)
    if spec is not None:
        label = spec.label
    else:
        # A user-added server (`models add-endpoint`) is a valid key target.
        try:
            endpoint = ProviderRegistry().endpoint(provider)
        except KeyError as exc:
            print(f"Unknown provider '{provider}'.")
            print(f"Known: {', '.join(sorted(API_KEY_PROVIDERS))}, plus your custom endpoints.")
            return 2
        if endpoint.auth_mode is not AuthMode.API_KEY:
            print(f"'{provider}' needs no API key (auth mode: {endpoint.auth_mode}); nothing saved.")
            return 2
        label = endpoint.label

    key = api_key or getpass.getpass(f"{label} API key: ").strip()
    if not key:
        print("No key provided; nothing saved.")
        return 2
    broker.save_api_key(provider, key, label=label)
    print(f"Saved {label} key to the macOS Keychain.")
    return 0


async def _login_oauth(broker: AuthBroker, provider: str, *, open_browser: bool) -> int:
    from openagent.provider.auth.flow import sign_in_interactive

    spec = all_specs()[provider]
    print(
        f"\nSigning in to {spec.label}.\n"
        "This uses the vendor's own sign-in flow and is NOT an official third-party\n"
        "integration: it can stop working, and vendors may revoke it. API keys remain\n"
        "the supported path.\n"
    )
    try:
        tokens = await sign_in_interactive(spec, open_browser=open_browser)
    except OAuthError as exc:
        print(f"\nSign-in failed: {exc}\n")
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130

    # Store under the spec key (anthropic_oauth), which is what resolve() reads
    # for a subscription ModelRef and what keeps it separate from an API key.
    broker.save_tokens(provider, tokens, label=spec.label)
    who = " · ".join(p for p in (tokens.account_id, tokens.plan_type) if p)
    print(f"\nSigned in to {spec.label}" + (f" as {who}" if who else "") + ".")
    print("Token stored in the macOS Keychain; refresh is automatic.\n")
    return 0


# -------------------------------------------------------------------- models

def handle_models(args: argparse.Namespace) -> int:
    """Sync entry point for a plain CLI. Runs its own event loop."""
    return _run(handle_models_async(args))


async def handle_models_async(args: argparse.Namespace) -> int:
    registry = ProviderRegistry()
    catalog = ModelCatalog(registry)

    if args.models_action == "list":
        if args.discover:
            found = await catalog.discover()
            running = len(found)
            print(
                f"Discovered models from {running} model server(s)"
                if running else "No model server answered (LM Studio, Ollama, MLX, llama.cpp, vLLM, your endpoints)."
            )
            for provider, reason in sorted(catalog.discovery_errors.items()):
                print(f"  ! {provider}: {reason}")
        refs = catalog.all_models()
        _print_models(refs)
        _print_default(registry, catalog)
        return 0

    if args.models_action == "probe":
        ref = _resolve_key(catalog, args.key)
        if ref is None:
            return 2
        result = await catalog.probe(ref)
        if result.usable:
            print(f"✓ {ref.key}: tool calling works ({result.latency_ms:.0f} ms)")
            return 0
        print(f"✗ {ref.key}: {result.error}")
        return 1

    if args.models_action == "use":
        return _use(catalog, args.key, getattr(args, "role", None), getattr(args, "policy", None))

    if args.models_action == "add-endpoint":
        return _add_endpoint(args)

    if args.models_action == "remove-endpoint":
        return _remove_endpoint(args)

    return 2


def _add_endpoint(args: argparse.Namespace) -> int:
    entry = {
        "name": args.name,
        "base_url": args.base_url,
        "wire_api": args.wire_api,
        "label": args.label or args.name,
        "default_model": args.default_model or "",
        "requires_key": args.requires_key,
        "local": args.local,
        "context_window": args.context_window,
    }
    try:
        add_endpoint_config(entry)
    except ValueError as exc:
        print(f"Cannot add endpoint: {exc}")
        return 2

    print(f"Added endpoint '{entry['name']}' at {entry['base_url']} ({entry['wire_api']}).")
    if entry["requires_key"]:
        print(f"  Store its key with: auth login {entry['name']} --api-key <key>")
    if not entry["local"]:
        print(
            "  Marked remote: privacy_lock will not route to it. If inference stays on\n"
            "  your network, re-add with --local."
        )
    print("  Discover its models with: models list --discover")
    return 0


def _remove_endpoint(args: argparse.Namespace) -> int:
    try:
        removed = remove_endpoint_config(args.name)
    except ValueError as exc:
        print(f"Cannot remove endpoint: {exc}")
        return 2
    if not removed:
        print(f"No custom endpoint named '{args.name}'.")
        return 1
    print(f"Removed endpoint '{args.name}'. Its key stays in the Keychain; "
          f"clear it with: auth logout {args.name}")
    return 0


def _run(coro) -> int:
    """Run ``coro`` from sync code, tolerating an already-running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "handle_models() needs an event loop; await handle_models_async() instead"
    )


def _resolve_key(catalog: ModelCatalog, key: str) -> ModelRef | None:
    ref = catalog.get(key)
    if ref is not None:
        return ref
    # Allow "provider model" form.
    if "/" not in key:
        print(f"Expected provider/model, got '{key}'.")
        return None
    provider, model = key.split("/", 1)
    try:
        return catalog.registry.ref(provider, model)
    except KeyError as exc:
        print(str(exc))
        return None


def _print_models(refs: list[ModelRef]) -> None:
    if not refs:
        print("No models known. Start a local server and run `models list --discover`.")
        return
    print(f"\n{'MODEL':<52} {'AUTH':<18} {'TOOLS':<7} {'CTX':>8}  COST")
    print("-" * 96)
    for ref in refs:
        tools = "probed" if ref.caps.probed and ref.caps.tools else ("yes" if ref.caps.tools else "no")
        cost = ref.cost.kind
        if ref.cost.kind == "per_token" and ref.cost.input_per_mtok is not None:
            cost = f"${ref.cost.input_per_mtok:g}/${ref.cost.output_per_mtok:g}/M"
        print(f"{ref.key:<52} {str(ref.auth_mode):<18} {tools:<7} {ref.caps.context_window:>8}  {cost}")
    print()


def _print_default(registry: ProviderRegistry, catalog: ModelCatalog) -> None:
    from openagent.core.config import get_config

    config = get_config()
    router = ModelRouter(catalog)
    router.rules.default_key = config.models.default_model
    router.rules.roles = dict(config.models.roles)
    try:
        router.rules.policy = RoutingPolicy(config.models.policy)
    except ValueError:
        pass
    print(f"Policy: {router.rules.policy}")
    print(f"Default: {router.rules.default_key or '(none)'}")
    for role, key in sorted(router.rules.roles.items()):
        print(f"  role {role}: {key}")
    try:
        chosen = router.select()
        print(f"\nSelected for the main loop: {chosen.key} ({'local' if chosen.is_local else 'cloud'})")
        if router.rules.default_key and chosen.key != router.rules.default_key:
            # The user set this default. Saying "selected: something else" without
            # saying why is the kind of thing that wastes an afternoon.
            print(
                f"  '{router.rules.default_key}' is not in the catalog — is its server running?\n"
                f"  Re-discover with: models list --discover, verify with: models probe {router.rules.default_key}"
            )
    except Exception as exc:
        print(f"\nNo model selectable: {exc}")
    print()


def _use(catalog: ModelCatalog, key: str, role: str | None, policy: str | None) -> int:
    from openagent.core.config import ConfigManager

    ref = _resolve_key(catalog, key)
    if ref is None:
        return 2

    updates: dict[str, Any] = {}
    if policy:
        updates["policy"] = policy
    if role:
        updates["role"] = role
        updates["model"] = key
    else:
        updates["default_model"] = key

    ConfigManager.update_config(**updates)
    target = f"role '{role}'" if role else "default model"
    print(f"Set {target} to {key} ({'local' if ref.is_local else 'cloud'}).")
    if not ref.caps.probed:
        print("Capabilities are declared, not probed. Verify with: models probe " + key)
    return 0
