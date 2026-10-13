"""Tests for SecretManager: macOS Keychain backend, file fallback, migration."""

from __future__ import annotations

import json
import stat

import pytest

from openagent.core.config import SecretManager


@pytest.fixture
def secret_file(tmp_path, monkeypatch):
    """Point the file backend at a temp dir (never the repo's secrets/)."""
    folder = tmp_path / "secrets"
    folder.mkdir()
    path = folder / "secrets.json"
    monkeypatch.setattr(SecretManager, "get_secret_folder", classmethod(lambda cls: folder))
    monkeypatch.setattr(SecretManager, "get_secret_file", classmethod(lambda cls: path))
    return path


@pytest.fixture
def fake_keychain(monkeypatch):
    """In-memory stand-in for the macOS Keychain."""
    store: dict[str, str] = {}

    def _get(account):
        return store.get(account)

    def _set(account, secret):
        store[account] = secret
        return True

    def _delete(account):
        return store.pop(account, None) is not None

    monkeypatch.setattr(SecretManager, "keychain_available", classmethod(lambda cls: True))
    monkeypatch.setattr(SecretManager, "_keychain_get", classmethod(lambda cls, a: _get(a)))
    monkeypatch.setattr(SecretManager, "_keychain_set", classmethod(lambda cls, a, s: _set(a, s)))
    monkeypatch.setattr(SecretManager, "_keychain_delete", classmethod(lambda cls, a: _delete(a)))
    monkeypatch.setattr(
        SecretManager, "_keychain_accounts", classmethod(lambda cls: sorted(store))
    )
    return store


@pytest.fixture
def file_only(monkeypatch):
    monkeypatch.setattr(SecretManager, "keychain_available", classmethod(lambda cls: False))


# --- file backend ----------------------------------------------------------


def test_file_backend_round_trip(secret_file, file_only):
    assert SecretManager.set_secret("demo_key", "v1") is True
    assert SecretManager.get_secret("demo_key") == "v1"
    assert SecretManager.set_secret("demo_key", "v2") is True
    assert SecretManager.get_secret("demo_key") == "v2"
    assert SecretManager.delete_secret("demo_key") is True
    assert SecretManager.get_secret("demo_key") is None


def test_file_backend_sets_owner_only_permissions(secret_file, file_only):
    SecretManager.set_secret("demo_key", "v1")
    mode = secret_file.stat().st_mode
    assert stat.S_IMODE(mode) == 0o600


def test_file_backend_writes_valid_json_on_first_use(secret_file, file_only):
    # Regression: the old implementation chmod'ed the file before creating it,
    # so the very first save raised FileNotFoundError.
    assert not secret_file.exists()
    SecretManager.set_secret("first_key", "v1")
    assert secret_file.exists()
    assert json.loads(secret_file.read_text()) == {"first_key": "v1"}


def test_corrupt_secrets_file_is_tolerated(secret_file, file_only):
    secret_file.write_text("{not json")
    assert SecretManager.get_secret("anything") is None


# --- environment precedence ------------------------------------------------


def test_environment_wins_over_storage(secret_file, file_only, monkeypatch):
    SecretManager.set_secret("demo_key", "stored")
    monkeypatch.setenv("DEMO_KEY", "from-env")
    assert SecretManager.get_secret("demo_key") == "from-env"


def test_get_api_key_reads_provider_env_var(secret_file, file_only, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "env-key")
    assert SecretManager.get_api_key("tavily") == "env-key"


def test_api_key_shims_use_provider_scoped_name(secret_file, file_only):
    SecretManager.save_api_key("Tavily", "k1")
    assert SecretManager.get_secret("tavily_api_key") == "k1"
    assert SecretManager.get_api_key("tavily") == "k1"
    assert SecretManager.delete_api_key("tavily") is True
    assert SecretManager.get_api_key("tavily") is None


# --- keychain backend ------------------------------------------------------


def test_keychain_round_trip(secret_file, fake_keychain):
    assert SecretManager.set_secret("demo_key", "v1") is True
    assert fake_keychain["demo_key"] == "v1"
    assert SecretManager.get_secret("demo_key") == "v1"
    assert SecretManager.set_secret("demo_key", "v2") is True
    assert SecretManager.get_secret("demo_key") == "v2"
    assert SecretManager.delete_secret("demo_key") is True
    assert SecretManager.get_secret("demo_key") is None


def test_keychain_write_never_persists_plaintext(secret_file, fake_keychain):
    SecretManager.set_secret("demo_key", "top-secret")
    assert not secret_file.exists() or "top-secret" not in secret_file.read_text()


def test_legacy_bare_key_migrates_to_keychain(secret_file, fake_keychain):
    # Legacy files stored keys under a bare provider name, e.g. {"tavily": "..."}.
    secret_file.write_text(json.dumps({"tavily": "legacy-value"}))

    assert SecretManager.get_api_key("tavily") == "legacy-value"
    assert fake_keychain["tavily_api_key"] == "legacy-value"
    # The plaintext entry is gone from the file after migration.
    assert json.loads(secret_file.read_text()) == {}
    # Subsequent reads come from the keychain, not the file.
    assert SecretManager.get_api_key("tavily") == "legacy-value"


def test_delete_removes_from_both_backends(secret_file, fake_keychain):
    secret_file.write_text(json.dumps({"demo_key": "file-value"}))
    fake_keychain["demo_key"] = "kc-value"

    assert SecretManager.delete_secret("demo_key") is True
    assert "demo_key" not in fake_keychain
    assert json.loads(secret_file.read_text()) == {}


def test_keychain_write_failure_falls_back_to_file(secret_file, monkeypatch):
    monkeypatch.setattr(SecretManager, "keychain_available", classmethod(lambda cls: True))
    monkeypatch.setattr(SecretManager, "_keychain_get", classmethod(lambda cls, a: None))
    monkeypatch.setattr(SecretManager, "_keychain_set", classmethod(lambda cls, a, s: False))
    monkeypatch.setattr(SecretManager, "_keychain_delete", classmethod(lambda cls, a: False))

    assert SecretManager.set_secret("demo_key", "v1") is True
    assert SecretManager.get_secret("demo_key") == "v1"
    assert json.loads(secret_file.read_text()) == {"demo_key": "v1"}


# --- a half-written keychain item -----------------------------------------
#
# The `security` CLI reads the secret from stdin a line at a time and can exit 0
# having stored a prefix of it. Two things follow, and both are tested here: the
# read-back check has to *remove* what it found, and a value that is already
# stored and unreadable has to stop shadowing the copy that works.


def _fake_security(stored: dict[str, str], limit: int = 1024):
    """Stand in for `/usr/bin/security`, storing at most `limit` characters."""

    def run(cmd, **kwargs):
        class Result:
            returncode = 0
            stderr = ""
            stdout = ""

        account = cmd[cmd.index("-a") + 1]
        if cmd[1] == "add-generic-password":
            stored[account] = kwargs["input"].splitlines()[0][:limit]
        elif cmd[1] == "find-generic-password":
            Result.stdout = stored.get(account, "")
        elif cmd[1] == "delete-generic-password":
            stored.pop(account, None)
        return Result()

    return run


def test_readback_mismatch_removes_the_half_written_item(secret_file, monkeypatch):
    monkeypatch.setattr(SecretManager, "keychain_available", classmethod(lambda cls: True))
    stored: dict[str, str] = {}
    monkeypatch.setattr("openagent.core.config.subprocess.run", _fake_security(stored))

    long_secret = "x" * 4281  # an OAuth payload is this order of magnitude
    assert SecretManager.set_secret("auth:openai_oauth", long_secret) is True
    # The truncated item is gone, and the whole payload is in the file backend.
    assert stored == {}
    assert json.loads(secret_file.read_text())["auth:openai_oauth"] == long_secret
    assert SecretManager.get_secret("auth:openai_oauth") == long_secret


def test_quarantine_drops_the_keychain_copy_and_keeps_the_file_copy(secret_file, fake_keychain):
    secret_file.write_text(json.dumps({"auth:openai_oauth": "good"}))
    fake_keychain["auth:openai_oauth"] = "{truncated"

    assert SecretManager.quarantine_secret("auth:openai_oauth") == "good"
    assert "auth:openai_oauth" not in fake_keychain
    # The copy that works is still stored, so the next read finds it.
    assert SecretManager.get_secret("auth:openai_oauth") == "good"


def test_quarantine_drops_a_file_entry_that_cannot_be_used(secret_file, file_only):
    secret_file.write_text(json.dumps({"auth:openai_oauth": "{truncated"}))

    assert SecretManager.quarantine_secret("auth:openai_oauth") is None
    assert json.loads(secret_file.read_text()) == {}


def test_list_secrets_returns_names_not_values(secret_file, fake_keychain):
    SecretManager.set_secret("alpha_key", "secret-a")
    SecretManager.set_secret("beta_key", "secret-b")
    names = SecretManager.list_secrets()
    assert {"alpha_key", "beta_key"} <= set(names)
    assert "secret-a" not in names


def test_parse_acct_names_from_dump_keychain_output():
    dump = (
        'keychain: "/Users/x/Library/Keychains/login.keychain-db"\n'
        "version: 51\n"
        'class: "genp"\n'
        "attributes:\n"
        '    0x00000007 <blob>="openagent"\n'
        '    "acct"<blob>="tavily_api_key"\n'
        '    "svce"<blob>="openagent"\n'
        'class: "genp"\n'
        "attributes:\n"
        '    "acct"<blob>="anthropic_oauth"\n'
    )
    assert SecretManager._parse_acct_names(dump) == ["tavily_api_key", "anthropic_oauth"]


def test_keychain_available_is_false_off_macos(monkeypatch):
    monkeypatch.setattr("openagent.core.config.sys.platform", "linux")
    assert SecretManager.keychain_available() is False
