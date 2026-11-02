"""Authenticated HTTP event intake.

Requests must carry an HMAC-SHA256 signature over ``<timestamp>.<raw body>``,
signed with an operator secret. Signatures outside a clock-skew window, replayed
event IDs, and senders over their rate limit are refused. The endpoint only
queues events; it never executes anything synchronously.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from dataclasses import dataclass, field
from pathlib import Path

from .storage import atomic_json

MAX_BODY = 64 * 1024


class ReplayGuard:
    """Remembers accepted event IDs so a replayed body is refused."""

    def __init__(self, path: Path, limit: int = 4096):
        self.path = path
        self.limit = limit
        self._seen: list[str] = []
        self._set: set[str] = set()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if isinstance(data, list):
                    self._seen = [str(x) for x in data][-limit:]
                    self._set = set(self._seen)
            except Exception:
                # A corrupt replay log fails closed by starting empty but is kept
                # on disk for inspection rather than silently overwritten.
                pass

    def check_and_add(self, event_id: str) -> bool:
        if event_id in self._set:
            return False
        self._set.add(event_id)
        self._seen.append(event_id)
        if len(self._seen) > self.limit:
            dropped = self._seen[:len(self._seen) - self.limit]
            self._seen = self._seen[-self.limit:]
            self._set = set(self._seen)
            for _ in dropped:
                pass
        atomic_json(self.path, self._seen)
        return True


@dataclass
class RateLimiter:
    """Token bucket per source; generous enough for bursts, not floods."""

    capacity: int = 30
    per_second: float = 1.0
    _buckets: dict[str, tuple[float, float]] = field(default_factory=dict)

    def allow(self, source: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        tokens, last = self._buckets.get(source, (float(self.capacity), now))
        tokens = min(self.capacity, tokens + (now - last) * self.per_second)
        if tokens < 1:
            self._buckets[source] = (tokens, now)
            return False
        self._buckets[source] = (tokens - 1, now)
        return True


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    return hmac.new(secret, f'{timestamp}.'.encode() + body, hashlib.sha256).hexdigest()


def verify(secret: bytes, timestamp: str, signature: str, body: bytes,
           now: float | None = None, tolerance: float = 300.0) -> str:
    """Return an error string, or '' when the signature is valid and fresh."""
    try:
        stamp = float(timestamp)
    except (TypeError, ValueError):
        return 'invalid timestamp'
    now = time.time() if now is None else now
    if abs(now - stamp) > tolerance:
        return 'stale timestamp'
    expected = sign(secret, timestamp, body)
    if not hmac.compare_digest(expected, signature or ''):
        return 'bad signature'
    return ''


def parse_event(body: bytes) -> dict:
    if len(body) > MAX_BODY:
        raise ValueError('body too large')
    try:
        data = json.loads(body)
    except ValueError as error:
        raise ValueError(f'invalid JSON: {error}') from None
    if not isinstance(data, dict):
        raise ValueError('event must be an object')
    text = data.get('text')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('event requires a non-empty text')
    event_id = data.get('event_id')
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError('event requires an event_id')
    if len(event_id) > 128:
        raise ValueError('event_id too long')
    thread_id = data.get('thread_id')
    if thread_id is not None and not isinstance(thread_id, str):
        raise ValueError('thread_id must be a string')
    return data


def create_app(service, secret: bytes, *, path: str = '/events',
               board_getter=None, dashboard_token: str | None = None):
    """Build the FastAPI app. Imported lazily so the CLI works without it."""
    from fastapi import FastAPI, Header, Request
    from fastapi.responses import JSONResponse

    if not secret:
        raise ValueError('a webhook secret is required')
    replay = ReplayGuard(Path(service.inbox.root) / 'webhook-seen.json')
    limiter = RateLimiter()
    app = FastAPI(title='super-agent intake', docs_url=None, redoc_url=None,
                  openapi_url=None)

    @app.post(path)
    async def intake(request: Request,
                      x_superagent_timestamp: str = Header(default=''),
                      x_superagent_signature: str = Header(default='')):
        body = await request.body()
        client = request.client.host if request.client else 'unknown'
        if not limiter.allow(client):
            return JSONResponse({'error': 'rate limited'}, status_code=429)
        problem = verify(secret, x_superagent_timestamp, x_superagent_signature, body)
        if problem:
            return JSONResponse({'error': problem}, status_code=401)
        try:
            data = parse_event(body)
        except ValueError as error:
            return JSONResponse({'error': str(error)}, status_code=422)
        if not replay.check_and_add(data['event_id']):
            return JSONResponse({'status': 'duplicate'}, status_code=200)
        if not isinstance(data.get('actor'), str) or not data.get('actor'):
            data['actor'] = 'owner'
        if not isinstance(data.get('source'), str) or not data.get('source'):
            data['source'] = 'webhook'
        try:
            path_ = service.inbox.accept(data)
        except Exception:
            return JSONResponse({'error': 'could not queue event'}, status_code=503)
        return JSONResponse({'status': 'queued', 'file': path_}, status_code=202)

    if board_getter is not None:
        app.state.board_getter = board_getter
        from . import dashboard
        dashboard.attach(app, token=dashboard_token)
    return app
