"""Host-owned execution policy. Command approvals are exact and single-use.

Docker is an isolation boundary, not a guarantee against kernel vulnerabilities.
No network, credentials, host home, or Docker socket are mounted in containers.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import uuid
from dataclasses import dataclass

from .shell import run_shell, scoped_directory


@dataclass
class Approval:
    id: str
    ticket_id: str
    command: str
    directory: str
    answer: asyncio.Future


class Execution:
    def __init__(self, *, mode='deny', sandbox='host', image='python:3.12-slim', approval_timeout=120):
        if mode not in ('deny', 'ask', 'trusted') or sandbox not in ('host', 'docker'):
            raise ValueError('Invalid execution mode or sandbox')
        if not image or image.startswith('-'):
            raise ValueError('Invalid sandbox image')
        self.mode = mode
        self.sandbox = sandbox
        self.image = image
        self.approval_timeout = approval_timeout
        self.pending: dict[str, Approval] = {}

    def decide(self, request_id: str, allow: bool) -> str:
        request = self.pending.get(request_id)
        if request is None or request.answer.done():
            return 'Error: approval request no longer pending.'
        request.answer.set_result(allow)
        return 'Approved once.' if allow else 'Denied.'

    async def run(self, ticket_id: str, root: str, command: str, timeout=30, working_dir='', *, check=None):
        directory = scoped_directory(root, working_dir)
        if self.mode == 'deny':
            return 'Error: shell execution disabled by host. Ask the operator to enable approvals or sandbox execution.'
        if self.mode == 'ask':
            request_id = 'exec-' + uuid.uuid4().hex[:8]
            answer = asyncio.get_running_loop().create_future()
            self.pending[request_id] = Approval(request_id, ticket_id, command, directory, answer)
            try:
                approved = await asyncio.wait_for(answer, self.approval_timeout)
            except TimeoutError:
                return 'Error: command approval expired; nothing executed.'
            finally:
                self.pending.pop(request_id, None)
            if not approved:
                return 'Error: command denied by operator; nothing executed.'
        # Recheck after waiting for approval; the mandate could have changed.
        if check is not None and not check():
            return 'Error: ticket stopped or mandate revoked.'
        return await self.execute(root, directory, command, timeout)

    async def execute(self, root: str, directory: str, command: str, timeout=30):
        """Run an already authorized command (also used for operator-set checks)."""
        if self.sandbox == 'host':
            return await run_shell(command, directory, timeout)
        if shutil.which('docker') is None:
            return 'Error: Docker not installed; sandbox execution refused.'
        from pathlib import Path

        root = str(Path(root).resolve())
        directory = scoped_directory(root, directory)
        name = 'solocoder-' + uuid.uuid4().hex
        workdir = '/work/' + str(Path(directory).relative_to(root))
        args = ['docker', 'run', '--rm', '--pull=never', '--name', name,
                '--network=none', '--read-only', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--pids-limit=128',
                '--memory=1g', '--cpus=1', '--user', f'{os.getuid()}:{os.getgid()}',
                '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m',
                '--mount', f'type=bind,src={root},dst=/work',
                '--workdir', workdir, self.image, '/bin/sh', '-c', command]
        # Do not interpolate model text into the host shell. Each argument is quoted.
        try:
            return await run_shell(shlex.join(args), root, timeout)
        finally:
            # Killing the Docker client alone does not kill its container.
            cleanup = asyncio.create_task(run_shell(
                shlex.join(['docker', 'rm', '-f', name]), root, 10))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
