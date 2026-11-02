"""Versioned, atomic JSON snapshots. Never deserialize executable objects."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path

from . import domain

_TYPES = {name: cls for name, cls in vars(domain).items()
          if isinstance(cls, type) and (is_dataclass(cls) or issubclass(cls, Enum))}


def encode(value):
    if isinstance(value, Enum):
        return {'$enum': type(value).__name__, 'value': value.value}
    if is_dataclass(value):
        return {'$type': type(value).__name__, 'fields':
                {f.name: encode(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, (set, frozenset, tuple)):
        return {'$collection': type(value).__name__, 'items': [encode(v) for v in value]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    return value


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if '$enum' in value:
        return _TYPES[value['$enum']](value['value'])
    if '$type' in value:
        return _TYPES[value['$type']](**{k: decode(v) for k, v in value['fields'].items()})
    if '$collection' in value:
        return {'set': set, 'frozenset': frozenset, 'tuple': tuple}[value['$collection']](
            decode(v) for v in value['items'])
    return {k: decode(v) for k, v in value.items()}


def atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.snapshot-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class StateStore:
    def __init__(self, workspace: str):
        self.path = Path(workspace).resolve() / 'state.json'

    def save(self, board: domain.Board) -> None:
        atomic_json(self.path, {'version': 1, 'board': encode({k: v for k, v in vars(board).items() if k not in ('_persist', '_mutation_depth')})})

    def load(self) -> domain.Board:
        data = json.loads(self.path.read_text())
        if data.get('version') != 1:
            raise ValueError('Unsupported super-agent state version')
        board = DurableBoard()
        board.__dict__.update(decode(data['board']))
        # Indexes must point to the canonical instances, not decoded duplicates.
        board._triggers = {t.id: t for thread in board.threads.values() for t in thread.triggers}
        board._assignments = {a.id: a for t in board.tickets.values() for a in t.assignments}
        for thread in board.threads.values():
            thread.pending = [board._triggers[t.id] for t in thread.pending]
            thread.evaluating = False
        decided = {tid for d in board.decisions for tid in d.trigger_ids}
        for thread_id, batch in board._inflight_batches.items():
            thread = board.threads[thread_id]
            pending_ids = {t.id for t in thread.pending}
            thread.pending[:0] = [board._triggers[t.id] for t in batch
                                  if t.id not in decided and t.id not in pending_ids]
        board._inflight_batches.clear()
        for ticket in board.tickets.values():
            for assignment in ticket.assignments:
                if assignment.state is domain.AssignmentState.RUNNING:
                    board.finish_assignment(assignment.id)
                    if not ticket.ended:
                        board.report(ticket.id, 'blocked',
                                     'Host restarted during execution. Inspect files, then retry explicitly; '
                                     'external side effects may already have happened.')
        return board


def attach_store(board: domain.Board, store: StateStore) -> None:
    """Checkpoint synchronous domain transitions before the next model call.

    Single-writer ownership is enforced by the CLI workspace lock.
    """
    board._persist = lambda: store.save(board)


def _durable(method):
    from functools import wraps

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        depth = getattr(self, '_mutation_depth', 0)
        self._mutation_depth = depth + 1
        try:
            result = method(self, *args, **kwargs)
        finally:
            self._mutation_depth = depth
        persist = getattr(self, '_persist', None)
        if persist is not None and depth == 0:
            persist()
        return result
    return wrapped


class DurableBoard(domain.Board):
    """Board whose public transitions checkpoint when attached to a store."""


for _name in ('submit', 'route', 'record_unavailable', 'stop', 'subscribe',
              'start_assignment', 'finish_assignment', 'ack', 'report', 'reply', 'finish_verification',
              'heartbeat', 'stall', 'schedule_retry', 'launch_due'):
    setattr(DurableBoard, _name, _durable(getattr(domain.Board, _name)))
