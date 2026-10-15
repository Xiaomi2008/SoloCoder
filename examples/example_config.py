"""Example: Config file support.

SoloCoder reads settings from ~/.solo-coder.yml, environment variables,
and CLI arguments with the following priority:
    CLI args > env vars > config file > defaults

Example config file (~/.solo-coder.yml):
    model: claude-sonnet-4
    max_turns: 30
    base_url: http://localhost:8080/v1
    auto_save: /tmp/solo-coder-session.json
    enable_learning: false
    max_messages: 40

Usage:
    # Using config from file/env:
    python examples/example_config.py

    # Overriding with command-line args:
    python examples/example_config.py --model gpt-4o
"""

from __future__ import annotations

import asyncio

from openagent.core.config import Config


def main():
    # Load config: file defaults -> env vars
    file_config = Config.from_file()
    config = file_config.apply_env()

    print("Current configuration:")
    for key, value in config.to_dict().items():
        print(f"  {key}: {value}")

    # Override programmatically
    config = config.override(model="gpt-4o", max_turns=10)
    print("\nAfter override:")
    for key, value in config.to_dict().items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
