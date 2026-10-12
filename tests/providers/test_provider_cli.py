"""CLI wiring for `auth` and `models`."""

from __future__ import annotations

import argparse

import pytest

from openagent.provider import cli
from openagent.provider.auth.broker import AuthBroker
from openagent.provider.auth.spec import ANTHROPIC_OAUTH, OPENAI_OAUTH


@pytest.fixture
def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    cli.add_subcommands(subparsers)
    return parser


def test_subcommands_register(parser):
    args = parser.parse_args(["auth", "status"])
    assert args.command == "auth" and args.auth_action == "status"
    args = parser.parse_args(["models", "list", "--discover"])
    assert args.command == "models" and args.discover is True
    args = parser.parse_args(["models", "use", "lmstudio/qwen3-coder-32b", "--role", "compact"])
    assert args.key == "lmstudio/qwen3-coder-32b" and args.role == "compact"


def test_provider_aliases_do_the_obvious_thing():
    assert cli._provider_key("claude") == ANTHROPIC_OAUTH
    assert cli._provider_key("chatgpt") == OPENAI_OAUTH
    assert cli._provider_key("codex") == OPENAI_OAUTH
    assert cli._provider_key("anthropic") == "anthropic"


class FakeStore:
    """Stands in for the macOS Keychain.

    Deliberately not a dict subclass: an empty dict is falsy, and a broker that
    treats a falsy store as "no store" silently writes to the real Keychain.
    """

    def __init__(self):
        self.data = {}

    def get_secret(self, name):
        return self.data.get(name)

    def set_secret(self, name, value):
        self.data[name] = value
        return True

    def delete_secret(self, name):
        return self.data.pop(name, None) is not None


def test_auth_status_shows_both_credential_classes(parser, capsys, monkeypatch):
    broker = AuthBroker(store=FakeStore(), env={})  # nothing configured
    monkeypatch.setattr(cli, "AuthBroker", lambda *a, **kw: broker)

    assert cli.handle_auth(parser.parse_args(["auth", "status"])) == 0
    out = capsys.readouterr().out
    assert "API keys (officially supported)" in out
    # The subscription path must be labelled as what it is.
    assert "unofficial, at your own risk" in out
    assert "not set" in out
    assert "not signed in" in out


def test_models_list_prints_the_catalog(parser, capsys):
    assert cli.handle_models(parser.parse_args(["models", "list"])) == 0
    out = capsys.readouterr().out
    assert "anthropic/claude-sonnet-4-5" in out
    assert "TOOLS" in out


def test_models_list_reports_when_no_server_is_running(parser, capsys, monkeypatch):
    async def no_servers(*a, **kw):
        return {}

    monkeypatch.setattr(cli.ModelCatalog, "discover", no_servers)
    cli.handle_models(parser.parse_args(["models", "list", "--discover"]))
    assert "No local model servers responded" in capsys.readouterr().out


def test_models_use_writes_the_default_model(parser, monkeypatch):
    from openagent.core.config import ConfigManager

    updates = {}
    monkeypatch.setattr(ConfigManager, "update_config", classmethod(lambda cls, **kw: updates.update(kw)))

    rc = cli.handle_models(parser.parse_args(["models", "use", "lmstudio/qwen3-coder-32b"]))
    assert rc == 0
    assert updates["default_model"] == "lmstudio/qwen3-coder-32b"


def test_models_use_with_a_role_writes_the_role_map(parser, monkeypatch):
    from openagent.core.config import ConfigManager

    updates = {}
    monkeypatch.setattr(ConfigManager, "update_config", classmethod(lambda cls, **kw: updates.update(kw)))

    cli.handle_models(parser.parse_args(["models", "use", "mlx/x", "--role", "compact"]))
    assert updates["role"] == "compact"
    assert updates["model"] == "mlx/x"


def test_models_use_rejects_an_unknown_provider(parser, capsys):
    rc = cli.handle_models(parser.parse_args(["models", "use", "nosuch/model"]))
    assert rc == 2
    assert "Unknown provider" in capsys.readouterr().out


def test_models_use_rejects_a_bare_model_name(parser, capsys):
    rc = cli.handle_models(parser.parse_args(["models", "use", "gpt-4o"]))
    assert rc == 2
    assert "provider/model" in capsys.readouterr().out


def test_api_key_login_saves_to_the_store(capsys, monkeypatch):
    from openagent.core.config import SecretManager

    def never_write_to_the_real_keychain(*a, **kw):
        raise AssertionError("a test wrote to the real macOS Keychain")

    monkeypatch.setattr(SecretManager, "set_secret", classmethod(never_write_to_the_real_keychain))

    broker = AuthBroker(store=FakeStore(), env={})
    args = argparse.Namespace(auth_action="login", provider="anthropic", api_key="sk-abc")

    original = cli.AuthBroker
    cli.AuthBroker = lambda *a, **kw: broker
    try:
        assert cli.handle_auth(args) == 0
    finally:
        cli.AuthBroker = original

    assert "Keychain" in capsys.readouterr().out
    assert broker.load_credentials("anthropic").api_key == "sk-abc"


def test_a_falsy_store_is_still_the_store(monkeypatch):
    """An empty store is a store, not an absent one."""
    from openagent.core.config import SecretManager

    class Empty(FakeStore):
        def __len__(self):
            return 0

    def never(*a, **kw):
        raise AssertionError("falsy store fell through to the real Keychain")

    monkeypatch.setattr(SecretManager, "set_secret", classmethod(never))
    store = Empty()
    broker = AuthBroker(store=store, env={})
    broker.save_api_key("anthropic", "sk-x")
    assert store.data["auth:anthropic"]
