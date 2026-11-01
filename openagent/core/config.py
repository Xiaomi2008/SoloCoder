"""Configuration management for OpenAgent."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.logging import logger

KEYCHAIN_SERVICE = "openagent"
_SECURITY_BIN = "/usr/bin/security"


@dataclass
class SearchConfig:
    """Search engine configuration."""

    provider: str = "tavily"  # Default to tavily
    tavily_api_key: str | None = None
    tavily_max_results: int = 5
    duckduck_max_results: int = 5
    fallback_to_duckduck: bool = True


@dataclass
class ModelConfig:
    """Which models to use, and how to pick among them.

    ``default_model`` is a ModelRef key (``provider/model``). ``roles`` maps a
    role (main, subagent, compact, title, verify) to the same form, letting a
    cheap local model handle compaction while the main loop uses a frontier one.
    """

    policy: str = "local_first"  # local_first | cloud_first | cheapest_capable | privacy_lock | fixed
    default_model: str = ""
    roles: dict[str, str] = field(default_factory=dict)
    # User-added model servers, e.g.
    #   {"name": "nas", "base_url": "http://192.168.4.34:8080/v1",
    #    "wire_api": "openai-chat", "local": True, "label": "NAS box"}
    # ``local: True`` means "inference stays on my network", which is what lets
    # privacy_lock accept a LAN server the user owns.
    custom_endpoints: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Config:
    """Main configuration for OpenAgent."""

    search: SearchConfig = field(default_factory=SearchConfig)
    models: ModelConfig = field(default_factory=ModelConfig)
    _config_path: Path | None = field(default=None, init=False)

    def to_dict(self) -> dict[str, Any]:
        """Export config to dictionary."""
        return {
            "search": {
                "provider": self.search.provider,
                "tavily_max_results": self.search.tavily_max_results,
                "duckduck_max_results": self.search.duckduck_max_results,
                "fallback_to_duckduck": self.search.fallback_to_duckduck,
            },
            "models": {
                "policy": self.models.policy,
                "default_model": self.models.default_model,
                "roles": dict(self.models.roles),
                "custom_endpoints": [dict(e) for e in self.models.custom_endpoints],
            },
        }


class SecretManager:
    """Manages sensitive data like API keys and OAuth tokens.

    Storage backends, in priority order:

    1. **macOS Keychain** (``/usr/bin/security``) on macOS. Secrets never touch
       the filesystem in plaintext. This is the backend the Mac app shares with
       the agent service, so a token signed in from the app is usable by the CLI.
    2. A ``0600`` JSON file, for Linux/CI where no keychain exists.

    Legacy plaintext keys found in ``secrets/secrets.json`` are migrated to the
    keychain on first read and removed from the file.
    """

    @classmethod
    def get_secret_folder(cls) -> Path:
        """The directory holding the file backend's secrets.

        Under the config directory, and **not** derived from ``__file__``. A
        packaged app runs from inside a ``.app`` bundle, where the package's
        parent is ``Contents/Resources``: creating a folder there breaks the code
        signature's seal, and on a Gatekeeper-translocated launch the bundle is
        read-only, so the ``mkdir`` raises and every keychain miss takes the
        exception path. Nothing is created on a read — ``_save_secrets`` makes
        the directory when it writes.
        """
        return Path.home() / ".openagent" / "secrets"

    @classmethod
    def get_secret_file(cls) -> Path:
        """The file backend's secrets file path."""
        return cls.get_secret_folder() / "secrets.json"

    @classmethod
    def get_legacy_secret_file(cls) -> Path:
        """Where the file backend's secrets lived: beside the source tree.

        Read-only, for migration. It is derived from ``__file__`` because that is
        the historical location — a checkout's ``secrets/secrets.json`` — and it
        is never created, so the read-only-bundle problem above does not apply.
        """
        return Path(__file__).parent.parent.parent / "secrets" / "secrets.json"

    # --- backend selection -------------------------------------------------

    @classmethod
    def keychain_available(cls) -> bool:
        """True when the macOS Keychain CLI is usable on this platform."""
        return sys.platform == "darwin" and Path(_SECURITY_BIN).exists()

    # --- macOS Keychain backend -------------------------------------------

    @classmethod
    def _keychain_get(cls, account: str) -> str | None:
        result = subprocess.run(
            [_SECURITY_BIN, "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        return value or None

    @classmethod
    def _keychain_set(cls, account: str, secret: str) -> bool:
        """Add or update a keychain item.

        Notes on the ``security`` CLI contract, learned the hard way:
        - ``-w`` must be the LAST flag: it takes an optional argument, so a
          trailing ``-U`` is swallowed as the password.
        - With ``-w`` and no argument it prompts "password data for new item:"
          then "retype password for new item:". A single stdin line fails the
          retype with "passwords don't match" and stores nothing while still
          exiting 0 — so the secret is written twice.
        - stdin (not argv) keeps the secret out of the process argument list.
        """
        result = subprocess.run(
            [
                _SECURITY_BIN, "add-generic-password", "-U",
                "-s", KEYCHAIN_SERVICE, "-a", account, "-w",
            ],
            input=f"{secret}\n{secret}\n",
            capture_output=True,
            text=True,
            # Without this, a run from a terminal prompts on that terminal
            # ("password data for new item:") instead of reading the pipe:
            # the CLI asks /dev/tty first. A new session has no controlling tty.
            start_new_session=True,
        )
        if result.returncode != 0:
            logger.debug(f"Keychain write failed for {account}: {result.stderr.strip()}")
            return False
        # The CLI can exit 0 without storing the whole value — a long secret is
        # read from stdin a line at a time, and what lands can be a prefix. So
        # confirm by reading back, and when it does not match, remove the item.
        # Leaving it behind is worse than the failed write: a half-written payload
        # reads back non-empty on every later call, so it shadows the intact copy
        # the file backend keeps, and the provider is wedged. Seen live — a
        # ChatGPT sign-in printed "Signed in", and `auth status` then reported
        # "Ignoring corrupt credential entry" for the token sitting in the file.
        if cls._keychain_get(account) == secret:
            return True
        logger.debug(f"Keychain read-back mismatch for {account}; removing the item")
        cls._keychain_delete(account)
        return False

    @classmethod
    def _keychain_delete(cls, account: str) -> bool:
        result = subprocess.run(
            [_SECURITY_BIN, "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    # --- file backend (non-macOS fallback) --------------------------------

    @classmethod
    def _load_secrets(cls) -> dict[str, str]:
        """Load secrets from the file backend, current path then legacy."""
        secret_file = cls.get_secret_file()
        if not secret_file.exists():
            # The migration path: a checkout's ``secrets/secrets.json``. Read
            # only — the caller that finds a legacy entry writes it to the
            # keychain and removes it from this file.
            secret_file = cls.get_legacy_secret_file()
        if secret_file.exists():
            try:
                with open(secret_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Could not load secrets file: {e}")
                return {}
        return {}

    @classmethod
    def _save_secrets(cls, secrets: dict[str, str]) -> None:
        """Save secrets to file with restricted permissions."""
        secret_file = cls.get_secret_file()
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        with open(secret_file, "w") as f:
            json.dump(secrets, f, indent=2)
        os.chmod(secret_file, 0o600)  # Owner read/write only

    # --- public API -------------------------------------------------------

    @staticmethod
    def _legacy_names(name: str) -> list[str]:
        """Legacy file keys that may hold this secret (bare provider names)."""
        names = [name]
        if name.endswith("_api_key"):
            names.append(name[: -len("_api_key")])
        return names

    @classmethod
    def get_secret(cls, name: str) -> str | None:
        """Get a secret by name, preferring the environment, then the keychain."""
        env_key = os.environ.get(name.upper())
        if env_key:
            logger.debug(f"Using {name} from environment variable")
            return env_key

        if cls.keychain_available():
            value = cls._keychain_get(name)
            if value:
                logger.debug(f"Using {name} from macOS Keychain")
                return value
            # One-time migration of a legacy plaintext entry.
            secrets = cls._load_secrets()
            legacy = None
            for legacy_name in cls._legacy_names(name):
                legacy = secrets.pop(legacy_name, None)
                if legacy:
                    break
            if legacy:
                if cls._keychain_set(name, legacy):
                    cls._save_secrets(secrets)
                    logger.info(f"Migrated {name} from plaintext secrets file to Keychain")
                    return legacy
                return legacy
            return None

        value = cls._load_secrets().get(name)
        if value:
            logger.debug(f"Using {name} from secrets file")
        return value

    @classmethod
    def set_secret(cls, name: str, value: str) -> bool:
        """Store a secret. Returns False when the backend rejected it."""
        if cls.keychain_available():
            ok = cls._keychain_set(name, value)
            if ok:
                secrets = cls._load_secrets()
                secrets.pop(name, None)
                cls._save_secrets(secrets)
                logger.info(f"Stored {name} in macOS Keychain")
                return True
            logger.warning("Keychain write failed; falling back to the file backend")

        secrets = cls._load_secrets()
        secrets[name] = value
        cls._save_secrets(secrets)
        logger.info(f"Stored {name} in the secrets file")
        return True

    @classmethod
    def delete_secret(cls, name: str) -> bool:
        """Remove a secret from every backend we may have written it to."""
        removed = False
        if cls.keychain_available() and cls._keychain_delete(name):
            removed = True
        secrets = cls._load_secrets()
        if name in secrets:
            del secrets[name]
            cls._save_secrets(secrets)
            removed = True
        if removed:
            logger.info(f"Deleted {name}")
        return removed

    @classmethod
    def quarantine_secret(cls, name: str) -> str | None:
        """Drop the entry ``get_secret`` would have returned, and return the next one.

        A value that fails to parse is not a credential, and leaving it in place
        is not neutral: ``get_secret`` prefers the keychain, so a half-written
        keychain item shadows the intact copy the file backend kept, every read
        returns the unusable value, ``auth status`` reports "not signed in" for a
        token that is on disk, and the only way out is `auth logout`.

        The caller knows the value is unusable; this knows which backend produced
        it. So it removes exactly that one — the keychain's when the keychain has
        one, the file's otherwise — and returns the copy the *other* backend
        holds, left in place so the next read works. When the file's copy is the
        unusable one there is nothing left to hand back, and it is removed.
        """
        if cls.keychain_available() and cls._keychain_get(name):
            cls._keychain_delete(name)
            return cls._load_secrets().get(name)

        secrets = cls._load_secrets()
        removed = False
        for legacy_name in cls._legacy_names(name):
            if secrets.pop(legacy_name, None):
                removed = True
        if removed:
            cls._save_secrets(secrets)
        return None

    @staticmethod
    def _parse_acct_names(dump_output: str) -> list[str]:
        """Extract account names from ``security dump-keychain`` output.

        Attribute lines look like:     "acct"<blob>="tavily_api_key"
        """
        return re.findall(r'"acct"<blob>="([^"]+)"', dump_output)

    @classmethod
    def _keychain_accounts(cls) -> list[str]:
        result = subprocess.run(
            [_SECURITY_BIN, "dump-keychain", "-s", KEYCHAIN_SERVICE],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        return cls._parse_acct_names(result.stdout)

    @classmethod
    def list_secrets(cls) -> list[str]:
        """Names of stored secrets (values are never returned)."""
        names: set[str] = set(cls._load_secrets())
        if cls.keychain_available():
            names.update(cls._keychain_accounts())
        return sorted(names)

    # --- API-key shims (existing call sites) ------------------------------

    @classmethod
    def get_api_key(cls, provider: str) -> str | None:
        """Get API key for a provider from environment, keychain, or secrets file."""
        provider = provider.lower()

        env_key = os.environ.get(f"{provider.upper()}_API_KEY")
        if env_key:
            logger.debug(f"Using {provider} API key from environment variable")
            return env_key

        return cls.get_secret(f"{provider}_api_key")

    @classmethod
    def save_api_key(cls, provider: str, api_key: str) -> bool:
        """Save API key for a provider."""
        return cls.set_secret(f"{provider.lower()}_api_key", api_key)

    @classmethod
    def delete_api_key(cls, provider: str) -> bool:
        """Delete API key for a provider."""
        return cls.delete_secret(f"{provider.lower()}_api_key")


class ConfigManager:
    """Manages application configuration."""

    CONFIG_FILE = Path.home() / ".openagent" / "config.json"

    @classmethod
    def get_config_path(cls) -> Path:
        """Get the config file path."""
        return cls.CONFIG_FILE

    @classmethod
    def load_config(cls) -> Config:
        """Load configuration from file."""
        try:
            if cls.CONFIG_FILE.exists():
                with open(cls.CONFIG_FILE, "r") as f:
                    data = json.load(f)

                config = Config(
                    search=SearchConfig(
                        provider=data.get("search", {}).get("provider", "tavily"),
                        tavily_max_results=data.get("search", {}).get(
                            "tavily_max_results", 5
                        ),
                        duckduck_max_results=data.get("search", {}).get(
                            "duckduck_max_results", 5
                        ),
                        fallback_to_duckduck=data.get("search", {}).get(
                            "fallback_to_duckduck", True
                        ),
                    ),
                    models=ModelConfig(
                        policy=data.get("models", {}).get("policy", "local_first"),
                        default_model=data.get("models", {}).get("default_model", ""),
                        roles=dict(data.get("models", {}).get("roles") or {}),
                        custom_endpoints=[
                            dict(e) for e in (data.get("models", {}).get("custom_endpoints") or [])
                        ],
                    ),
                )
                logger.info(f"Loaded config from {cls.CONFIG_FILE}")
                return config
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Could not load config file: {e}")

        # Return default config
        config = Config()
        logger.info("Using default configuration")
        return config

    @classmethod
    def save_config(cls, config: Config) -> None:
        """Save configuration to file."""
        cls.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

        with open(cls.CONFIG_FILE, "w") as f:
            json.dump(config.to_dict(), f, indent=2)

        logger.info(f"Saved config to {cls.CONFIG_FILE}")

    @classmethod
    def update_config(cls, **kwargs) -> Config:
        """Update configuration with new values."""
        config = cls.load_config()

        if "provider" in kwargs:
            config.search.provider = kwargs["provider"]
        if "tavily_max_results" in kwargs:
            config.search.tavily_max_results = kwargs["tavily_max_results"]
        if "duckduck_max_results" in kwargs:
            config.search.duckduck_max_results = kwargs["duckduck_max_results"]
        if "fallback_to_duckduck" in kwargs:
            config.search.fallback_to_duckduck = kwargs["fallback_to_duckduck"]
        if "policy" in kwargs:
            config.models.policy = kwargs["policy"]
        if "default_model" in kwargs:
            config.models.default_model = kwargs["default_model"]
        if "roles" in kwargs:
            config.models.roles = dict(kwargs["roles"] or {})
        if "custom_endpoints" in kwargs:
            config.models.custom_endpoints = [dict(e) for e in (kwargs["custom_endpoints"] or [])]
        if "role" in kwargs and "model" in kwargs:
            config.models.roles = {**config.models.roles, kwargs["role"]: kwargs["model"]}

        cls.save_config(config)
        # Keep the process-wide instance in step with the file: the Mac app
        # writes settings and then reads get_config() in the same process.
        reload_config()
        return config


# Global config instance (lazy-loaded)
_config: Config | None = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = ConfigManager.load_config()
    return _config


def init_config() -> Config:
    """Initialize the configuration (called at app startup)."""
    global _config
    _config = ConfigManager.load_config()

    # Auto-load API keys from secrets
    provider = _config.search.provider
    api_key = SecretManager.get_api_key(provider)
    if api_key:
        _config.search.tavily_api_key = api_key
        logger.info(f"Initialized {provider} provider with loaded API key")
    else:
        logger.warning(
            f"No API key found for {provider}. Set it with:"
            f"\n  openagent config set-api-key tavily <your-api-key>"
        )

    return _config


def reload_config() -> Config:
    """Re-read the config file into the global instance."""
    global _config
    _config = ConfigManager.load_config()
    return _config


def reset_config() -> None:
    """Reset configuration to defaults."""
    global _config
    _config = Config()
