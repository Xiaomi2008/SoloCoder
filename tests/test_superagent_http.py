"""End-to-end HTTP intake + dashboard using FastAPI's TestClient."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from openagent.apps.superagent import Board
from openagent.apps.superagent.dashboard import attach
from openagent.apps.superagent.service import Inbox
from openagent.apps.superagent.webhooks import create_app, sign

SECRET = b'k' * 32
TOKEN = 'dash-token'


class Service:
    def __init__(self, tmp_path):
        self.inbox = Inbox(str(tmp_path))


class App:
    def __init__(self):
        self.board = Board()


def make(tmp_path, dashboard=False):
    app = App()
    return create_app(Service(tmp_path), SECRET,
                      board_getter=(lambda: app.board) if dashboard else None,
                      dashboard_token=TOKEN), app


def post(client, body: dict | bytes, *, stamp=None, signature=None, path='/events'):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    stamp = stamp or str(int(time.time()))
    return client.post(path, content=raw,
                       headers={'X-SuperAgent-Timestamp': stamp,
                                'X-SuperAgent-Signature': signature or sign(SECRET, stamp, raw)})


def test_signed_event_is_queued(tmp_path):
    app, _ = make(tmp_path)
    client = TestClient(app)
    response = post(client, {'text': 'Build it', 'event_id': 'e1'})
    assert response.status_code == 202
    assert list(Service(tmp_path).inbox.inbox.glob('*.json'))


def test_unsigned_and_bad_signature_rejected(tmp_path):
    client = TestClient(make(tmp_path)[0])
    raw = json.dumps({'text': 'x', 'event_id': 'e2'}).encode()
    assert client.post('/events', content=raw).status_code == 401
    assert post(client, {'text': 'x', 'event_id': 'e2'}, signature='nope').status_code == 401


def test_stale_timestamp_rejected(tmp_path):
    client = TestClient(make(tmp_path)[0])
    assert post(client, {'text': 'x', 'event_id': 'e3'},
                stamp=str(int(time.time()) - 600)).status_code == 401


def test_replay_is_acknowledged_but_not_queued_twice(tmp_path):
    client = TestClient(make(tmp_path)[0])
    body = {'text': 'Once', 'event_id': 'dup'}
    assert post(client, body).status_code == 202
    assert post(client, body).json()['status'] == 'duplicate'


def test_invalid_payload_returns_422(tmp_path):
    client = TestClient(make(tmp_path)[0])
    assert post(client, {'event_id': 'x'}).status_code == 422
    assert post(client, b'not json').status_code == 422


def test_rate_limit_returns_429(tmp_path, monkeypatch):
    import openagent.apps.superagent.webhooks as wh
    monkeypatch.setattr(wh.RateLimiter, 'allow', lambda self, source: False)
    client = TestClient(make(tmp_path)[0])
    assert post(client, {'text': 'x', 'event_id': 'rl'}).status_code == 429


def test_dashboard_requires_token(tmp_path):
    client = TestClient(make(tmp_path, dashboard=True)[0])
    assert client.get('/').status_code == 401
    response = client.get('/', headers={'Authorization': f'Bearer {TOKEN}'})
    assert response.status_code == 200 and 'No tickets' in response.text


def test_dashboard_escapes_untrusted_text(tmp_path):
    app, holder = make(tmp_path, dashboard=True)
    trigger = holder.board.submit('<script>alert(1)</script>', actor='owner')
    holder.board.route(kind='accept', trigger_ids=trigger.id,
                       title='<script>alert(1)</script>')
    ticket = next(iter(holder.board.tickets.values()))
    client = TestClient(app)
    page = client.get('/', headers={'Authorization': f'Bearer {TOKEN}'}).text
    detail = client.get(f'/tickets/{ticket.id}', headers={'Authorization': f'Bearer {TOKEN}'}).text
    for rendered in (page, detail):
        assert '<script>alert(1)</script>' not in rendered
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in rendered


def test_dashboard_unknown_ticket_404(tmp_path):
    client = TestClient(make(tmp_path, dashboard=True)[0])
    response = client.get('/tickets/ticket-999', headers={'Authorization': f'Bearer {TOKEN}'})
    assert response.status_code == 404


def test_no_openapi_surface(tmp_path):
    client = TestClient(make(tmp_path)[0])
    assert client.get('/openapi.json').status_code == 404
    assert client.get('/docs').status_code == 404


def test_webhook_and_dashboard_share_one_app(tmp_path):
    client = TestClient(make(tmp_path, dashboard=True)[0])
    response = post(client, {'text': 'Both surfaces', 'event_id': 'shared'})
    assert response.status_code == 202
    assert client.get('/', headers={'Authorization': f'Bearer {TOKEN}'}).status_code == 200
    assert client.get('/threads', headers={'Authorization': f'Bearer {TOKEN}'}).status_code == 200
