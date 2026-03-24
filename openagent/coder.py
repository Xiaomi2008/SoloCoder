"""Compatibility layer for the SoloCoder app package."""

from __future__ import annotations

import asyncio

from openagent.apps.solocoder.agent import CoderAgent, create_coder

__all__ = ["CoderAgent", "create_coder"]


if __name__ == "__main__":
    from openagent.apps.solocoder.cli import main

    asyncio.run(main())
