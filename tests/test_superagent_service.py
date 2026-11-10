"""Durable, idempotent service intake and notification outbox."""
import asyncio
import json
import shlex
import sys
from types import SimpleNamespace

import pytest

from openagent.apps.superagent.domain import Board, HumanReply
from openagent.apps.superagent.service import Inbox, run_hook
from openagent.apps.superagent.service_runtime import ServiceRuntime
from tests.test_superagent_reliability import Router, accepted


class Runtime:
    def __init__(self, board):
        self.board = board
        self.replies = []

    def checkpoint(self):
        pass

    async def run_pending(self):
        replies, self.replies = self.replies, []
        return replies


def service(tmp_path, **kwargs):
    board = Board(workspace=str(tmp_path))
    runtime = Runtime(board)
    return board, runtime, ServiceRuntime(runtime, str(tmp_path), poll_interval=.01, **kwargs)


def drop(inbox, text, **fields):
    return inbox.accept({'text': text, **fields})


async def test_event_is_submitted_and_moved(tmp_path):
    board, runtime, svc = service(tmp_path)
    drop(svc.inbox, 'Build a game', actor='owner')
    await svc.poll_once()
    assert len(board.threads) == 1
    assert not list(svc.inbox.inbox.glob('*.json'))
    assert len(list(svc.inbox.done.glob('*.json'))) == 1
    await svc.poll_once()
    assert len(board.threads) == 1  # same event was not replayed


@pytest.mark.parametrize('event,error', [
    ({'text': 7}, 'string text'),
    ({'text': '   '}, 'empty'),
    ({'actor': 'owner'}, 'string text'),
])
async def test_invalid_events_are_quarantined(tmp_path, event, error):
    board, _, svc = service(tmp_path)
    svc.inbox.accept(event)
    await svc.poll_once()
    failures = [p for p in svc.inbox.failed.glob('*.json') if not p.name.endswith('.error.json')]
    assert len(failures) == 1
    assert error in failures[0].with_suffix('.error.json').read_text()
    assert not board.threads


async def test_bad_json_is_quarantined(tmp_path):
    _, _, svc = service(tmp_path)
    (svc.inbox.inbox / 'broken.json').write_text('{not json')
    await svc.poll_once()
    assert [p for p in svc.inbox.failed.glob('*.json') if not p.name.endswith('.error.json')]
    assert 'Expecting property name' in next(svc.inbox.failed.glob('*.error.json')).read_text()


async def test_notifications_are_appended(tmp_path):
    _, runtime, svc = service(tmp_path)
    runtime.replies = [HumanReply('ticket-4', 'attention', 'Pick one', 'Which?')]
    await svc.drain()
    records = [json.loads(line) for line in svc.inbox.outbox.read_text().splitlines()]
    assert records[0]['question'] == 'Which?'


async def test_hook_receives_json_on_stdin(tmp_path):
    script = tmp_path / 'hook.py'
    script.write_text('import json,sys\nprint(json.load(sys.stdin)["text"])\n')
    command = f'{shlex.quote(sys.executable)} {shlex.quote(str(script))}'
    assert run_hook(command, {'text': 'hello'}) == 'ok'
    assert run_hook('/nonexistent/hook', {}) .startswith('hook failed')


async def test_service_forever_stops(tmp_path):
    _, _, svc = service(tmp_path)
    drop(svc.inbox, 'One')
    stop = asyncio.Event()
    runner = asyncio.create_task(svc.run_forever(stop))
    for _ in range(100):
        if list(svc.inbox.done.glob('*.json')):
            break
        await asyncio.sleep(.01)
    stop.set()
    await asyncio.wait_for(runner, 1)
    assert list(svc.inbox.done.glob('*.json'))


async def test_service_does_not_replay_a_done_event(tmp_path):
    board, runtime, svc = service(tmp_path)
    drop(svc.inbox, 'One')
    await svc.poll_once()
    await svc.poll_once()
    assert len(board.threads) == 1


async def test_hook_error_is_notified(tmp_path):
    _, _, svc = service(tmp_path, hook='/definitely/missing')
    svc.emit({'type': 'reply', 'text': 'Hello'})
    records = [json.loads(line) for line in svc.inbox.outbox.read_text().splitlines()]
    assert records[0]['type'] == 'reply'
    assert records[-1]['type'] == 'hook_error'
