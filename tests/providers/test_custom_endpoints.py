"""User-added model servers: config entries, routing, and the CLI around them.

The case that matters: a LAN inference box (http://192.168.4.34:8080/v1) that is
OpenAI-compatible, needs a key, and is "local" in the privacy sense but not in
the loopback sense.
"""

from __future__ import annotations

import argparse

import pytest

from openagent.core.config import ConfigManager
from openagent.provider.cli import handle_models
from openagent.provider.model_ref import AuthMode, Capabilities, ModelRef, WireAPI
from openagent.provider.registry import (
    BUILTIN_PROVIDERS,
    ProviderRegistry,
    add_endpoint_config,
    custom_specs,
    remove_endpoint_config,
    spec_from_config,
)

NAS = {
    "name": "nas",
    "base_url": "http://192.168.4.34:8080/v1",
    "wire_api": "openai-chat",
    "label": "NAS box",
    "requires_key": True,
    "local": True,
    "context_window": 65536,
}


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Config reads/writes go to a temp file, and the global instance is reset."""
    from openagent.core import config as cfg

    path = tmp_path / "config.json"
    monkeypatch.setattr(ConfigManager, "CONFIG_FILE", path)
    monkeypatch.setattr(cfg, "_config", None)
    yield path
    monkeypatch.setattr(cfg, "_config", None)


@pytest.fixture
def fake_keychain(monkeypatch, tmp_path):
    """In-memory Keychain stand-in; the file backend also points at tmp_path."""
    from openagent.core.config import SecretManager

    store: dict[str, str] = {}
    folder = tmp_path / "secrets"
    folder.mkdir()
    path = folder / "secrets.json"

    monkeypatch.setattr(SecretManager, "keychain_available", classmethod(lambda cls: True))
    monkeypatch.setattr(SecretManager, "_keychain_get", classmethod(lambda cls, a: store.get(a)))
    monkeypatch.setattr(
        SecretManager,
        "_keychain_set",
        classmethod(lambda cls, a, s: (store.__setitem__(a, s), True)[1]),
    )
    monkeypatch.setattr(SecretManager, "_keychain_delete", classmethod(lambda cls, a: store.pop(a, None)))
    monkeypatch.setattr(SecretManager, "get_secret_folder", classmethod(lambda cls: folder))
    monkeypatch.setattr(SecretManager, "get_secret_file", classmethod(lambda cls: path))
    return store


# ------------------------------------------------------------------ parsing


def test_a_lan_server_parses_into_an_endpoint_spec():
    spec = spec_from_config(NAS)
    assert spec.provider == "nas"
    assert spec.base_url == "http://192.168.4.34:8080/v1"
    assert spec.wire_api is WireAPI.OPENAI_CHAT
    assert spec.local is True
    # A key-protected LAN server is an API-key endpoint: 'local' is a privacy
    # claim about where inference runs, not an auth mode.
    assert spec.auth_mode is AuthMode.API_KEY
    assert spec.default_caps.context_window == 65536


def test_a_server_without_a_key_is_a_local_endpoint():
    spec = spec_from_config({"name": "box", "base_url": "http://10.0.0.5:8080/v1", "requires_key": False})
    assert spec.auth_mode is AuthMode.LOCAL


@pytest.mark.parametrize(
    "entry, reason",
    [
        ({"name": "NAS Box", "base_url": "http://x/v1"}, "name"),
        ({"name": "nas", "base_url": "192.168.4.34:8080"}, "base_url"),
        ({"name": "nas", "base_url": "ftp://host/v1"}, "base_url"),
        ({"name": "nas", "base_url": "http://x/v1", "wire_api": "grpc"}, "wire_api"),
    ],
)
def test_bad_entries_are_rejected_with_the_offending_field(entry, reason):
    with pytest.raises(ValueError) as exc:
        spec_from_config(entry)
    assert reason in str(exc.value)


# ---------------------------------------------------------------- registry


def test_registry_picks_up_custom_endpoints_from_config(config_file):
    add_endpoint_config(NAS)
    registry = ProviderRegistry()
    ref = registry.ref("nas", "qwen3-coder-30b")
    assert ref.key == "nas/qwen3-coder-30b"
    assert ref.base_url == "http://192.168.4.34:8080/v1"
    assert ref.is_local is True
    assert "nas" in [e.provider for e in registry.local_endpoints()]


def test_custom_endpoint_discovery_url_does_not_double_the_v1(config_file):
    add_endpoint_config(NAS)
    registry = ProviderRegistry()
    spec = registry.endpoint("nas")
    url = f"{registry.base_url_for(spec).rstrip('/')}{spec.discover_path}"
    assert url == "http://192.168.4.34:8080/v1/models"


def test_a_bad_custom_entry_is_skipped_not_fatal(config_file, caplog):
    from openagent.core.config import get_config

    add_endpoint_config(NAS)
    cfg = get_config()
    cfg.models.custom_endpoints.append({"name": "broken", "base_url": "nope"})
    specs = custom_specs()
    names = [s.provider for s in specs]
    assert "nas" in names and "broken" not in names


def test_add_endpoint_refuses_to_shadow_a_builtin(config_file):
    with pytest.raises(ValueError, match="built-in provider"):
        add_endpoint_config({"name": "lmstudio", "base_url": "http://10.0.0.9:1234/v1"})


def test_remove_endpoint_refuses_builtins_and_removes_custom(config_file):
    add_endpoint_config(NAS)
    with pytest.raises(ValueError, match="built-in provider"):
        remove_endpoint_config("anthropic")
    assert remove_endpoint_config("nas") is True
    assert remove_endpoint_config("nas") is False
    assert "nas" not in [s.provider for s in custom_specs()]


# ----------------------------------------------------------------- routing

from openagent.provider.model_ref import CostHint, CostKind  # noqa: E402
from openagent.provider.router import ModelRouter, NoCapableModel, RoutingPolicy, RoutingRules  # noqa: E402


def _ref_from(spec) -> ModelRef:
    return ModelRef(
        model=spec.default_model or "m",
        provider=spec.provider,
        wire_api=spec.wire_api,
        auth_mode=spec.auth_mode,
        base_url=spec.base_url,
        label=spec.label,
        caps=Capabilities(tools=True, streaming=True, context_window=spec.default_caps.context_window),
        cost=CostHint(CostKind.LOCAL) if spec.local else CostHint(CostKind.PER_TOKEN),
        is_local=spec.local,
    )


class FakeCatalog:
    def __init__(self, refs):
        self._refs = list(refs)

    def get(self, key):
        return next((r for r in self._refs if r.key == key), None)

    def all_models(self):
        return list(self._refs)

    def probe_result(self, ref):
        return None


def _router(refs, policy):
    return ModelRouter(FakeCatalog(refs), RoutingRules(policy=policy))


def test_privacy_lock_accepts_a_trusted_lan_endpoint(config_file):
    add_endpoint_config(NAS)
    spec = ProviderRegistry().endpoint("nas")
    chosen = _router([_ref_from(spec)], RoutingPolicy.PRIVACY_LOCK).select(Capabilities(tools=True))
    assert chosen.key == "nas/m"


def test_privacy_lock_refuses_a_custom_endpoint_marked_remote(config_file):
    add_endpoint_config({**NAS, "local": False, "name": "cloudbox"})
    spec = ProviderRegistry().endpoint("cloudbox")
    with pytest.raises(NoCapableModel):
        _router([_ref_from(spec)], RoutingPolicy.PRIVACY_LOCK).select()


# ----------------------------------------------------- discovery diagnostics


class _Client:
    """Minimal httpx.AsyncClient stand-in that records request headers."""

    def __init__(self, behaviour, seen):
        self.behaviour = behaviour
        self.seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        self.seen.append((url, dict(headers or {})))
        return self.behaviour(url)


async def test_discovery_reports_a_server_that_wants_a_key(config_file, monkeypatch):
    """A key-protected LAN server must not show up as 'nothing is running'."""
    import httpx

    from openagent.provider.catalog import ModelCatalog

    add_endpoint_config(NAS)
    seen: list = []

    def behaviour(url):
        request = httpx.Request("GET", url)
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _Client(behaviour, seen))
    catalog = ModelCatalog(ProviderRegistry())
    found = await catalog.discover()

    assert found == {}
    assert "needs an API key" in catalog.discovery_errors["nas"]
    assert "auth login nas --api-key" in catalog.discovery_errors["nas"]


async def test_discovery_sends_the_stored_key(fake_keychain, config_file, monkeypatch):
    import httpx

    from openagent.provider.catalog import ModelCatalog

    add_endpoint_config(NAS)
    registry = ProviderRegistry()
    registry.broker.save_api_key("nas", "sk-lan-secret", label="NAS box")

    seen: list = []

    def behaviour(url):
        response = httpx.Response(200, json={"data": [{"id": "qwen3-coder-30b"}]}, request=httpx.Request("GET", url))
        return response

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _Client(behaviour, seen))
    catalog = ModelCatalog(registry)
    found = await catalog.discover()

    assert [r.key for r in found["nas"]] == ["nas/qwen3-coder-30b"]
    # Discovery fans out to every endpoint in parallel; pick ours out of the race.
    nas_headers = next(h for url, h in seen if url.startswith("http://192.168.4.34:8080"))
    assert nas_headers["Authorization"] == "Bearer sk-lan-secret"


async def test_discovery_names_the_host_when_nothing_listens(config_file, monkeypatch):
    import httpx

    from openagent.provider.catalog import ModelCatalog

    add_endpoint_config(NAS)
    seen: list = []

    def behaviour(url):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _Client(behaviour, seen))
    catalog = ModelCatalog(ProviderRegistry())
    await catalog.discover()

    assert "192.168.4.34:8080 is not answering" in catalog.discovery_errors["nas"]


# --------------------------------------------------------------------- cli


def _parser():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command")
    from openagent.provider.cli import add_subcommands

    add_subcommands(sub)
    return p


def test_cli_add_endpoint_writes_config_and_tells_you_the_next_step(config_file, capsys):
    args = _parser().parse_args(
        ["models", "add-endpoint", "nas", "--base-url", "http://192.168.4.34:8080/v1", "--local"]
    )
    assert handle_models(args) == 0
    out = capsys.readouterr().out
    assert "Added endpoint 'nas'" in out
    assert "auth login nas --api-key" in out
    assert ConfigManager.load_config().models.custom_endpoints[0]["name"] == "nas"


def test_cli_add_endpoint_warns_when_privacy_lock_will_not_use_it(config_file, capsys):
    args = _parser().parse_args(
        ["models", "add-endpoint", "remote1", "--base-url", "https://gpu.example.com/v1"]
    )
    handle_models(args)
    assert "privacy_lock" in capsys.readouterr().out


def test_cli_remove_endpoint(config_file, capsys):
    add_endpoint_config(NAS)
    args = _parser().parse_args(["models", "remove-endpoint", "nas"])
    assert handle_models(args) == 0
    assert "Removed endpoint 'nas'" in capsys.readouterr().out


def test_cli_auth_login_accepts_a_custom_endpoint(fake_keychain, config_file, capsys):
    from openagent.provider.cli import handle_auth

    add_endpoint_config(NAS)
    args = argparse.Namespace(auth_action="login", provider="nas", api_key="sk-lan-1", no_browser=True)
    assert handle_auth(args) == 0
    assert fake_keychain["auth:nas"]
    assert "NAS box" in capsys.readouterr().out


def test_cli_auth_login_rejects_an_unknown_provider(fake_keychain, config_file, capsys):
    from openagent.provider.cli import handle_auth

    args = argparse.Namespace(auth_action="login", provider="nope", api_key="sk-x", no_browser=True)
    assert handle_auth(args) == 2
    assert "Unknown provider" in capsys.readouterr().out


def test_auth_status_lists_custom_servers(fake_keychain, config_file, capsys):
    from openagent.provider.cli import handle_auth

    add_endpoint_config(NAS)
    handle_auth(argparse.Namespace(auth_action="login", provider="nas", api_key="sk-lan", no_browser=True))
    capsys.readouterr()
    handle_auth(argparse.Namespace(auth_action="status"))
    out = capsys.readouterr().out
    assert "Your model servers" in out
    assert "192.168.4.34:8080" in out
    assert "key set" in out and "local network" in out


def test_builtins_are_protected_from_removal():
    assert "anthropic" in BUILTIN_PROVIDERS and "lmstudio" in BUILTIN_PROVIDERS
