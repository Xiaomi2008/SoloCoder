"""Authenticated HTTP intake: signature, freshness, replay, limits, validation."""
import json
import time

import pytest

from openagent.apps.superagent.webhooks import (RateLimiter, ReplayGuard, parse_event,
                                               sign, verify)
from openagent.apps.superagent.service import Inbox

SECRET = b'super-secret-value'


def test_sign_and_verify_round_trip():
    body = b'{"text":"hi"}'
    stamp = str(int(time.time()))
    signature = sign(SECRET, stamp, body)
    assert verify(SECRET, stamp, signature, body) == ''
    assert verify(b'wrong', stamp, signature, body) == 'bad signature'
    assert verify(SECRET, stamp, 'deadbeef', body) == 'bad signature'


def test_stale_and_invalid_timestamp_refused():
    body = b'{}'
    old = str(int(time.time()) - 400)
    assert verify(SECRET, old, sign(SECRET, old, body), body) == 'stale timestamp'
    assert verify(SECRET, 'not-a-number', 'x', body) == 'invalid timestamp'
    fresh = str(int(time.time()) + 400)
    assert verify(SECRET, fresh, sign(SECRET, fresh, body), body) == 'stale timestamp'


@pytest.mark.parametrize('payload,error', [
    (b'[1,2]', 'object'),
    (b'{"text": ""}', 'non-empty text'),
    (b'{"text": "hi"}', 'event_id'),
    (b'{"text": "hi", "event_id": "a", "thread_id": 5}', 'thread_id'),
    (b'not json', 'JSON'),
])
def test_invalid_bodies_rejected(payload, error):
    with pytest.raises((ValueError, TypeError)) as caught:
        parse_event(payload)
    assert error.lower() in str(caught.value).lower()


def test_body_size_limit():
    with pytest.raises(ValueError, match='too large'):
        parse_event(b'{"text":"' + b'x' * (70 * 1024) + b'", "event_id":"a"}')


def test_replay_guard_dedupes_and_persists(tmp_path):
    path = tmp_path / 'seen.json'
    guard = ReplayGuard(path, limit=4)
    assert guard.check_and_add('a')
    assert not guard.check_and_add('a')
    assert guard.check_and_add('b')
    again = ReplayGuard(path, limit=4)
    assert not again.check_and_add('b')
    assert again.check_and_add('c')
    assert json.loads(path.read_text()) == ['a', 'b', 'c']


def test_replay_guard_evicts_oldest(tmp_path):
    guard = ReplayGuard(tmp_path / 'seen.json', limit=2)
    assert guard.check_and_add('1')
    assert guard.check_and_add('2')
    assert guard.check_and_add('3')
    assert guard.check_and_add('1')  # evicted, so new again


def test_rate_limiter_bursts_then_throttles():
    limiter = RateLimiter(capacity=3, per_second=0.0)
    assert [limiter.allow('a', now=0) for _ in range(3)] == [True] * 3
    assert limiter.allow('a', now=0) is False
    assert limiter.allow('b', now=0) is True


@pytest.mark.parametrize('now', [0, 1, 10])
def test_rate_limiter_refills(now):
    limiter = RateLimiter(capacity=1, per_second=1.0)
    assert limiter.allow('a', now=0)
    assert not limiter.allow('a', now=0)
    if now:
        assert limiter.allow('a', now=now)


def test_rejects_missing_secret():
    from openagent.apps.superagent.webhooks import create_app
    with pytest.raises(ValueError, match='secret'):
        create_app(object(), b'')
