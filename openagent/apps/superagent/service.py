"""Continuous intake: durable, idempotent event inbox and notification outbox.

No public webhook endpoint is exposed here. Files are the trust boundary: an
operator (or a trusted integration process) drops JSON events into inbox/.
Notifications are appended to a local JSONL outbox; optional command hooks run
with a fixed environment and are never given event text as shell code.
"""
from __future__ import annotations

import json
import shlex
import os
import subprocess
import uuid
from pathlib import Path

from .storage import atomic_json


class Inbox:
    def __init__(self, workspace: str):
        self.root = Path(workspace).resolve() / 'service'
        self.inbox = self.root / 'inbox'
        self.done = self.root / 'done'
        self.failed = self.root / 'failed'
        self.outbox = self.root / 'outbox.jsonl'
        for directory in (self.inbox, self.done, self.failed):
            directory.mkdir(parents=True, exist_ok=True)

    def events(self):
        for path in sorted(self.inbox.glob('*.json')):
            try:
                data = json.loads(path.read_text())
                yield path, data
            except Exception as error:
                self._finish(path, 'failed', {'error': str(error)})

    def accept(self, data):
        """Write one event; UUID makes the filename collision-resistant."""
        event_id = uuid.uuid4().hex
        data = dict(data)
        data['event_id'] = event_id
        atomic_json(self.inbox / f'{event_id}.json', data)
        return event_id

    def _finish(self, path, folder, note=None):
        target = getattr(self, folder) / path.name
        os.replace(path, target)
        if note is not None:
            atomic_json(target.with_suffix('.error.json'), note)

    def mark_done(self, path):
        self._finish(path, 'done')

    def fail(self, path, reason):
        self._finish(path, 'failed', {'error': str(reason)})

    def notify(self, record):
        with self.outbox.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())


def run_hook(command: str, record: dict, timeout: float = 30) -> str:
    """Run an operator-provided hook; JSON arrives on stdin, not in argv."""
    try:
        process = subprocess.run(shlex.split(command), input=json.dumps(record), text=True,
                                 capture_output=True, timeout=timeout, check=False,
                                 env={'PATH': os.environ.get('PATH', '/usr/bin:/bin')})
    except Exception as error:
        return f'hook failed: {error}'
    if process.returncode:
        return f'hook exit {process.returncode}: {process.stderr[:500]}'
    return 'ok'
