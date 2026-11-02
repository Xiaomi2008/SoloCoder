"""Headless service loop over the same runtime and durable Board."""
from __future__ import annotations

import asyncio
from pathlib import Path

from .service import Inbox, run_hook


class ServiceRuntime:
    def __init__(self, runtime, workspace: str, *, poll_interval=1.0, hook=None, actor='owner'):
        self.runtime = runtime
        self.inbox = Inbox(workspace)
        self.poll_interval = poll_interval
        self.hook = hook
        self.actor = actor
        self.stopping = False

    def checkpoint(self):
        self.runtime.checkpoint()

    async def drain(self):
        """One pass: intake events, run queued work, emit notifications."""
        replies = await self.runtime.run_pending()
        for reply in replies:
            self.emit({'type': 'reply', 'purpose': reply.purpose,
                       'ticket_id': reply.ticket_id, 'text': reply.text,
                       'question': reply.question})
        return replies

    def emit(self, record):
        self.inbox.notify(record)
        if self.hook:
            outcome = run_hook(self.hook, record)
            if outcome != 'ok':
                self.inbox.notify({'type': 'hook_error', 'error': outcome})

    async def poll_once(self):
        for path, data in self.inbox.events():
            try:
                if not isinstance(data, dict) or not isinstance(data.get('text'), str):
                    raise ValueError('event requires a string text field')
                text = data['text'].strip()
                if not text:
                    raise ValueError('event text is empty')
                actor = str(data.get('actor', self.actor))
                source = str(data.get('source', 'file'))
                thread_id = data.get('thread_id') or None
                self.runtime.board.submit(text, actor=actor, source=source, thread_id=thread_id)
                self.checkpoint()
                self.inbox.mark_done(path)
            except Exception as error:
                self.inbox.fail(path, error)
        return await self.drain()

    async def run_forever(self, stop: asyncio.Event | None = None):
        while stop is None or not stop.is_set():
            try:
                await self.poll_once()
            except Exception as error:
                self.emit({'type': 'service_error', 'error': f'{type(error).__name__}: {error}'})
            if stop is None:
                await asyncio.sleep(self.poll_interval)
            else:
                try:
                    await asyncio.wait_for(stop.wait(), self.poll_interval)
                except TimeoutError:
                    pass
