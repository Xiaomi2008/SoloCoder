"""Cancellable shell commands; POSIX process groups include shell descendants.

Working-directory checks are not an OS sandbox. Use only trusted deployments.
"""
from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path


async def run_shell(command: str, directory: str, timeout: float = 30) -> str:
    if timeout <= 0 or timeout > 120:
        return 'Error: timeout must be greater than zero and at most 120 seconds.'
    process = await asyncio.create_subprocess_shell(
        command, cwd=directory, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, start_new_session=True,
    )
    chunks = bytearray()

    async def drain():
        while chunk := await process.stdout.read(8192):
            # Drain continuously without keeping unlimited output in memory.
            if len(chunks) < 64000:
                chunks.extend(chunk[:64000-len(chunks)])
        await process.wait()

    async def cleanup():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()

    try:
        await asyncio.wait_for(drain(), timeout)
    except TimeoutError:
        await cleanup()
        return f'Error: command timed out after {timeout} seconds; process group killed.'
    except asyncio.CancelledError:
        await cleanup()
        raise
    finally:
        # Also clean up background descendants after the foreground shell exits.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    output = chunks.decode(errors='replace') or '(no output)'
    return f'Exit code: {process.returncode}\n{output}'


def scoped_directory(root: str, requested: str | None) -> str:
    root_path = Path(root).resolve()
    path = Path(requested).expanduser() if requested else root_path
    path = (root_path / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(root_path):
        raise ValueError('Working directory must be inside the ticket directory')
    return str(path)
