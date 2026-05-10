"""SoloCoder CLI wrappers."""

from __future__ import annotations

import asyncio

from cli_coder import (
    detect_provider,
    get_api_key_env_var,
    run_interactive_session,
    setup_argparse,
)


async def main() -> None:
    from cli_coder import main as legacy_main

    await legacy_main()


def run() -> None:
    asyncio.run(main())


__all__ = [
    "detect_provider",
    "get_api_key_env_var",
    "main",
    "run",
    "run_interactive_session",
    "setup_argparse",
]
