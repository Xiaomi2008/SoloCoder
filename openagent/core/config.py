"""Configuration management for OpenAgent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.logging import logger


@dataclass
class SearchConfig:
    """Search engine configuration."""

    provider: str = "tavily"  # Default to tavily
    tavily_api_key: str | None = None
    tavily_max_results: int = 5
    duckduck_max_results: int = 5
    fallback_to_duckduck: bool = True


@dataclass
class Config:
    """Main configuration for OpenAgent."""

    search: SearchConfig = field(default_factory=SearchConfig)
    _config_path: Path | None = field(default=None, init=False)

    def to_dict(self) -> dict[str, Any]:
        """Export config to dictionary."""
        return {
            "search": {
                "provider": self.search.provider,
                "tavily_max_results": self.search.tavily_max_results,
                "duckduck_max_results": self.search.duckduck_max_results,
                "fallback_to_duckduck": self.search.fallback_to_duckduck,
            }
        }


class SecretManager:
    """Manages sensitive data like API keys."""

    @classmethod
    def get_secret_folder(cls) -> Path:
        """Get or create the secret folder in project directory."""
        SECRET_FOLDER = Path(__file__).parent.parent.parent / "secrets"
        SECRET_FOLDER.mkdir(parents=True, exist_ok=True)
        return SECRET_FOLDER

    @classmethod
    def get_secret_file(cls) -> Path:
        """Get the secrets file path."""
        return cls.get_secret_folder() / "secrets.json"

    @classmethod
    def _load_secrets(cls) -> dict[str, str]:
        """Load secrets from file."""
        secret_file = cls.get_secret_file()
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
        with open(secret_file, "w") as f:
            os.chmod(secret_file, 0o600)  # Owner read/write only
            json.dump(secrets, f, indent=2)

    @classmethod
    def get_api_key(cls, provider: str) -> str | None:
        """Get API key for a provider from secrets file or environment."""
        provider = provider.lower()

        # First check environment variable
        env_var = f"{provider.upper()}_API_KEY"
        env_key = os.environ.get(env_var)
        if env_key:
            logger.debug(f"Using {provider} API key from environment variable")
            return env_key

        # Then check secrets file
        secrets = cls._load_secrets()
        key = secrets.get(provider)
        if key:
            logger.debug(f"Using {provider} API key from secrets file")
            return key

        logger.debug(f"No {provider} API key found")
        return None

    @classmethod
    def save_api_key(cls, provider: str, api_key: str) -> bool:
        """Save API key to secrets file."""
        secrets = cls._load_secrets()
        secrets[provider] = api_key
        cls._save_secrets(secrets)
        logger.info(f"Saved {provider} API key to secrets file")
        return True

    @classmethod
    def delete_api_key(cls, provider: str) -> bool:
        """Delete API key from secrets file."""
        secrets = cls._load_secrets()
        if provider in secrets:
            del secrets[provider]
            cls._save_secrets(secrets)
            logger.info(f"Deleted {provider} API key from secrets file")
            return True
        return False


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
                    )
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
        config.SECRET_FILE = cls.CONFIG_FILE
        config.SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)

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

        cls.save_config(config)
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


def reset_config() -> None:
    """Reset configuration to defaults."""
    global _config
    _config = Config()
