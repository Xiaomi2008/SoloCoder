"""AuthBroker: Keychain-backed credential resolution and token refresh."""

from __future__ import annotations

import time

import pytest

from openagent.provider.auth.broker import AuthBroker
from openagent.provider.auth.tokens import AuthRequired, Credentials, TokenSet
from openagent.provider.model_ref import AuthMode, ModelRef, WireAPI
from openagent.provider.registry import ProviderRegistry


class DictStore:
    """Stands in for the macOS Keychain so tests never touch the real one."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get_secret(self, name: str) -> str | None:
        return self.data.get(name)

    def set_secret(self, name: str, value: str) -> bool:
        self.data[name] = value
        return True

    def delete_secret(self, name: str) -> bool:
        return self.data.pop(name, None) is not None


@pytest.fixture
def store() -> DictStore:
    return DictStore()


@pytest.fixture
def broker(store) -> AuthBroker:
    return AuthBroker(store=store, env={})


def anthropic_ref() -> ModelRef:
    return ProviderRegistry().ref("anthropic", "claude-sonnet-4-5")


def oauth_ref() -> ModelRef:
    return ProviderRegistry().ref("anthropic_oauth")


# ------------------------------------------------------------------- storage

def test_the_injected_store_is_always_used(store, monkeypatch):
    """A test store must never fall through to the real macOS Keychain."""
    from openagent.core.config import SecretManager

    def never(*args, **kwargs):
        raise AssertionError("wrote to the real macOS Keychain")

    monkeypatch.setattr(SecretManager, "set_secret", classmethod(never))
    broker = AuthBroker(store=store, env={})
    broker.save_api_key("anthropic", "sk-x")
    assert broker.load_credentials("anthropic").api_key == "sk-x"


def test_credentials_round_trip_through_the_store(broker, store):
    broker.save_credentials(
        "anthropic_oauth",
        Credentials(
            mode=AuthMode.OAUTH_SUBSCRIPTION,
            tokens=TokenSet(access_token="a", refresh_token="r", account_id="acct", plan_type="max"),
            headers={"anthropic-beta": "oauth-2025-04-20"},
        ),
    )
    loaded = broker.load_credentials("anthropic_oauth")
    assert loaded.tokens.access_token == "a"
    assert loaded.tokens.account_id == "acct"
    assert loaded.headers["anthropic-beta"] == "oauth-2025-04-20"
    # Stored under one Keychain account name, as JSON.
    assert "auth:anthropic_oauth" in store.data
    assert "access_token" in store.data["auth:anthropic_oauth"]


def test_corrupt_entry_is_ignored_not_fatal(broker, store):
    store.data["auth:anthropic"] = "{not json"
    assert broker.load_credentials("anthropic") is None


def test_delete_credentials(broker, store):
    broker.save_api_key("anthropic", "sk-test")
    assert broker.delete_credentials("anthropic") is True
    assert broker.delete_credentials("anthropic") is False


# ----------------------------------------------------------------- resolution

def test_local_models_need_no_credential(broker):
    ref = ProviderRegistry().ref("lmstudio")
    assert broker.resolve(ref).mode is AuthMode.LOCAL


def test_api_key_resolves_from_environment(broker):
    env = {"ANTHROPIC_API_KEY": "sk-from-env", "OPENAI_PROJECT_ID": "proj_1"}
    broker_env = AuthBroker(store=DictStore(), env=env)
    creds = broker_env.resolve(anthropic_ref())
    assert creds.api_key == "sk-from-env"
    assert creds.source == "env"


def test_openai_project_and_org_become_headers(broker):
    env = {"OPENAI_API_KEY": "sk-x", "OPENAI_PROJECT_ID": "proj_1", "OPENAI_ORG_ID": "org_1"}
    broker_env = AuthBroker(store=DictStore(), env=env)
    creds = broker_env.resolve(ProviderRegistry().ref("openai"))
    assert creds.headers["OpenAI-Project"] == "proj_1"
    assert creds.headers["OpenAI-Organization"] == "org_1"


def test_keychain_beats_environment(broker, store):
    broker_env = AuthBroker(store=store, env={"ANTHROPIC_API_KEY": "sk-env"})
    broker_env.save_api_key("anthropic", "sk-keychain")
    assert broker_env.resolve(anthropic_ref()).api_key == "sk-keychain"


def test_missing_credential_raises_with_sign_in_hint(broker):
    with pytest.raises(AuthRequired) as excinfo:
        broker.resolve(anthropic_ref())
    assert excinfo.value.provider == "anthropic"
    assert "auth login" in str(excinfo.value)


def test_stored_api_key_is_not_used_for_an_oauth_ref(broker, store):
    """A key for the API provider must not silently satisfy a subscription ref."""
    broker.save_api_key("anthropic_oauth", "sk-should-not-be-used")
    # saved mode is api_key, ref wants oauth_subscription -> not a match
    with pytest.raises(AuthRequired):
        broker.resolve(oauth_ref())


def test_oauth_credentials_get_the_vendor_beta_header(broker):
    broker.save_credentials(
        "anthropic_oauth",
        Credentials(
            mode=AuthMode.OAUTH_SUBSCRIPTION,
            tokens=TokenSet(access_token="a", refresh_token="r", account_id="acct_7"),
        ),
    )
    creds = broker.resolve(oauth_ref())
    assert creds.headers["anthropic-beta"] == "oauth-2025-04-20"
    assert creds.headers["anthropic-account-id"] == "acct_7"


# ------------------------------------------------------------------ refresh

async def test_expired_token_is_refreshed_and_persisted(broker, store):
    broker.save_credentials(
        "anthropic_oauth",
        Credentials(
            mode=AuthMode.OAUTH_SUBSCRIPTION,
            tokens=TokenSet(access_token="old", refresh_token="refresh-me", expires_at=time.time() - 10),
        ),
    )

    async def refresher(spec, token):
        assert token == "refresh-me"
        return TokenSet(access_token="new", refresh_token=None, expires_at=time.time() + 3600)

    broker._refresher = refresher
    creds = await broker.refresh_if_needed(oauth_ref())
    assert creds.tokens.access_token == "new"
    # A refresh response without a refresh_token must not lose the old one.
    assert creds.tokens.refresh_token == "refresh-me"
    assert broker.load_credentials("anthropic_oauth").tokens.access_token == "new"


async def test_fresh_token_is_not_refreshed(broker):
    calls = []

    async def refresher(spec, token):
        calls.append(token)
        return TokenSet(access_token="new")

    broker.save_credentials(
        "anthropic_oauth",
        Credentials(
            mode=AuthMode.OAUTH_SUBSCRIPTION,
            tokens=TokenSet(access_token="still-valid", refresh_token="r", expires_at=time.time() + 3600),
        ),
    )
    broker._refresher = refresher
    creds = await broker.refresh_if_needed(oauth_ref())
    assert creds.tokens.access_token == "still-valid"
    assert calls == []


async def test_expired_without_refresh_token_demands_sign_in(broker):
    broker.save_credentials(
        "anthropic_oauth",
        Credentials(
            mode=AuthMode.OAUTH_SUBSCRIPTION,
            tokens=TokenSet(access_token="old", refresh_token=None, expires_at=time.time() - 1),
        ),
    )
    with pytest.raises(AuthRequired, match="sign in again"):
        await broker.refresh_if_needed(oauth_ref())


# ------------------------------------------------------------------- status

def test_status_covers_api_keys_and_subscriptions(broker):
    broker.save_credentials(
        "anthropic_oauth",
        Credentials(
            mode=AuthMode.OAUTH_SUBSCRIPTION,
            tokens=TokenSet(access_token="a", refresh_token="r", account_id="me@x.com", plan_type="max"),
        ),
    )
    rows = {r.provider: r for r in broker.status()}
    assert rows["anthropic"].configured is False
    assert rows["anthropic_oauth"].configured is True
    assert rows["anthropic_oauth"].account == "me@x.com"
    assert rows["anthropic_oauth"].plan == "max"
    # The subscription path is not an official integration; the UI needs the flag.
    assert rows["anthropic_oauth"].unofficial is True


def test_status_reads_api_key_from_env(broker):
    broker_env = AuthBroker(store=DictStore(), env={"OPENAI_API_KEY": "sk-env"})
    rows = {r.provider: r for r in broker_env.status()}
    assert rows["openai"].configured is True
    assert rows["openai"].source == "env"
