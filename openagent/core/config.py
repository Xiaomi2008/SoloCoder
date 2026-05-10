"""Configuration management for SoloCoder.

Reads settings from a YAML config file, environment variables, and CLI args.
Priority: CLI args > env vars > config file > defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Config:
    """Holds all configurable settings for the agent/CLI.

    Args:
        model: LLM model identifier
        api_key: API key (or empty string to use environment variable)
        base_url: Custom API base URL for OpenAI-compatible endpoints
        max_turns: Maximum conversation turns per request
        working_dir: Working directory for file operations
        system_prompt: Override system prompt
        auto_save: Path to auto-save session JSON after each turn
        enable_learning: Enable online learning features
        max_messages: Max messages before compression kicks in
    """

    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = ""
    max_turns: int = 20
    working_dir: str = ""
    system_prompt: str = ""
    auto_save: str = ""
    enable_learning: bool = False
    max_messages: int = 50

    @classmethod
    def from_file(cls, path: str | Path | None = None) -> "Config":
        """Load config from a YAML file.

        Args:
            path: Config file path. Defaults to ~/.solo-coder.yml.
        """
        if path is None:
            path = str(Path.home() / ".solo-coder.yml")

        config_path = Path(path)
        if not config_path.exists():
            return cls()

        try:
            import yaml
        except ImportError:
            # Fall back to basic parsing if PyYAML unavailable
            return cls()

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        # Map YAML keys to config fields
        field_map = {
            "model": str,
            "api_key": str,
            "base_url": str,
            "max_turns": int,
            "working_dir": str,
            "system_prompt": str,
            "auto_save": str,
            "enable_learning": bool,
            "max_messages": int,
        }

        kwargs: dict[str, Any] = {}
        for key, cast in field_map.items():
            if key in data:
                try:
                    kwargs[key] = cast(data[key])
                except (ValueError, TypeError):
                    pass  # Use default on bad type

        return cls(**kwargs)

    def override(self, **kwargs: Any) -> "Config":
        """Return a new Config with specified fields overridden, skipping falsy values."""
        overrides = {k: v for k, v in kwargs.items() if v}
        new_data = {**self.__dict__, **overrides}
        return Config(**new_data)

    def apply_env(self) -> "Config":
        """Override with environment variables if set."""
        env_map = {
            "model": "MODEL",
            "api_key": "API_KEY",
            "base_url": "BASE_URL",
            "max_turns": "MAX_TURNS",
            "working_dir": "WORKING_DIR",
            "auto_save": "AUTO_SAVE",
            "max_messages": "MAX_MESSAGES",
        }

        overrides = {}
        for field_name, env_suffix in env_map.items():
            env_key = f"SOLO_CODER_{env_suffix}"
            val = os.environ.get(env_key)
            if val:
                field_type = type(getattr(self, field_name))
                try:
                    overrides[field_name] = field_type(val)
                except (ValueError, TypeError):
                    pass

        if overrides:
            return self.override(**overrides)
        return self

    def to_dict(self) -> dict[str, Any]:
        """Serialize config to a dictionary."""
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
