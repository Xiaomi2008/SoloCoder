"""Read-only web dashboard. Every page is rendered from the persisted Board."""
from __future__ import annotations

import html
import time

from fastapi import Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from .terminal import clean


def _e(value) -> str:
    return html.escape(clean(value), quote=True)


def _page(title: str, body: str) -> str:
    return ('<!doctype html><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_e(title)}</title>'
            '<style>body{font:15px/1.5 system-ui,sans-serif;margin:2rem;max-width:60rem}'
            'table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;'
            'padding:.35rem;text-align:left;vertical-align:top}code{font-size:.9em}'
            '.person{color:#a60}.working{color:#06c}.ended{color:#777}'
            'pre{background:#f6f6f6;padding:.5rem;overflow:auto}</style>'
            f'<h1>{_e(title)}</h1>{body}')


def index(board) -> str:
    rows = ''.join(
        f'<tr><td><a href="/tickets/{_e(t.id)}">{_e(t.id)}</a></td>'
        f'<td>{_e(t.title)}</td><td class="person">{_e(str(compute_stage(t)))}</td>'
        f'<td>{_e(t.verification)}</td></tr>'
        for t in board.tickets.values())
    table = ('<table><tr><th>Ticket</th><th>Title</th><th>Stage</th><th>Verification</th></tr>'
             f'{rows}</table>') if rows else '<p>No tickets.</p>'
    return _page('Super-agent tickets', f'<p>{len(board.tickets)} tickets, '
                                        f'{len(board.threads)} threads</p>{table}')


def ticket(board, ticket_id: str) -> str | None:
    t = board.tickets.get(ticket_id)
    if t is None:
        return None
    evidence = ''.join(f'<pre>{_e(x)}</pre>' for x in t.verification_evidence)
    reports = ''.join(f'<li><b>{_e(r.kind.value)}</b> {_e(r.summary)}</li>'
                      for r in t.reports[-10:])
    body = (f'<p>Stage: <b class="person">{_e(str(compute_stage(t)))}</b> &middot; '
            f'Verification: <b>{_e(t.verification)}</b></p>'
            f'<p>Directory: <code>{_e(t.directory)}</code></p>'
            f'<p>Branch: <code>{_e(t.branch or "none")}</code></p>'
            + (f'<p>Progress: {_e(t.progress)}</p>' if t.progress else '')
            + (f'<p>Question: <b>{_e(t.attention)}</b></p>' if t.attention else '')
            + f'<h2>Verification evidence</h2>{evidence or "<p>None.</p>"}'
            + f'<h2>Recent reports</h2><ul>{reports or "<li>None.</li>"}</ul>'
            + f'<p><a href="/">Back</a></p>')
    return _page(f'{t.id} / {t.title}', body)


def threads(board) -> str:
    rows = ''.join(f'<tr><td>{_e(t.id)}</td><td>{_e(t.title)}</td>'
                   f'<td>{_e(", ".join(sorted(t.subscriptions)) or "none")}</td>'
                   f'<td>{len(t.triggers)}</td></tr>' for t in board.threads.values())
    table = ('<table><tr><th>Thread</th><th>Title</th><th>Tickets</th><th>Messages</th></tr>'
             f'{rows}</table>') if rows else '<p>No threads.</p>'
    return _page('Super-agent threads', f'<p><a href="/">Back</a></p>{table}')


def attach(app, *, token: str | None) -> None:
    """Mount read-only routes. With a token, every request must send it."""

    def _board() -> object:
        return app.state.board_getter()

    def require(authorization: str = Header(default='')) -> None:
        if token and authorization != f'Bearer {token}':
            raise HTTPException(status_code=401, detail='unauthorized')

    @app.get('/', response_class=HTMLResponse)
    async def root(request: Request, authorization: str = Header(default='')):
        require(authorization)
        return index(_board())

    @app.get('/tickets/{ticket_id}', response_class=HTMLResponse)
    async def one(ticket_id: str, request: Request, authorization: str = Header(default='')):
        require(authorization)
        page = ticket(_board(), ticket_id)
        if page is None:
            raise HTTPException(status_code=404, detail='no such ticket')
        return page

    @app.get('/threads', response_class=HTMLResponse)
    async def all_threads(request: Request, authorization: str = Header(default='')):
        require(authorization)
        return threads(_board())


from .domain import compute_stage  # noqa: E402  (kept at the end to avoid a cycle)
