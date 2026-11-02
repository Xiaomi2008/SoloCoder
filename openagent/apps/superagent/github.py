"""GitHub Issues connector: poll with a token, dedupe by issue number.

An issue body is untrusted task text. The label is the authorization gate: only
issues carrying the configured label are admitted, and only their body text
becomes a Trigger. No webhook payload is trusted; no comment commands execute.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .storage import atomic_json

API = 'https://api.github.com'


class GitHubIssues:
    def __init__(self, repo: str, label: str, *, token: str | None = None,
                 state_file: Path, base_url: str = API):
        if '/' not in repo or repo.count('/') != 1:
            raise ValueError('repo must be "owner/name"')
        self.repo = repo
        self.label = label
        self.token = token if token is not None else os.environ.get('GITHUB_TOKEN', '')
        self.state_file = state_file
        self.base_url = base_url
        self._seen: set[int] = set()
        if state_file.exists():
            try:
                self._seen = set(int(x) for x in json.loads(state_file.read_text()))
            except Exception:
                pass

    def headers(self) -> dict:
        return {'Accept': 'application/vnd.github+json',
                'Authorization': f'Bearer {self.token}',
                'X-GitHub-Api-Version': '2022-11-28'}

    async def fetch(self, limit: int = 20) -> list[dict]:
        import httpx
        params = {'labels': self.label, 'state': 'open', 'per_page': str(limit),
                  'sort': 'created', 'direction': 'desc'}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=20) as client:
            response = await client.get(f'/repos/{self.repo}/issues',
                                        params=params, headers=self.headers())
        response.raise_for_status()
        issues = response.json()
        out = []
        for issue in issues:
            if 'pull_request' in issue:  # PRs share this endpoint
                continue
            number = issue.get('number')
            if not isinstance(number, int):
                continue
            out.append(issue)
        return out

    def mark(self, number: int) -> None:
        self._seen.add(number)
        atomic_json(self.state_file, sorted(self._seen))

    async def poll(self, service, limit: int = 20) -> list[int]:
        """Queue unseen labeled issues; return their numbers."""
        queued = []
        for issue in await self.fetch(limit):
            number = issue['number']
            if number in self._seen:
                continue
            title = str(issue.get('title') or '').strip()
            body = str(issue.get('body') or '').strip()
            if not title:
                continue
            text = title if not body else f'{title}\n\n{body}'
            service.accept({
                'text': text,
                'actor': 'owner',
                'source': 'github',
                'event_id': f'gh-{self.repo.replace("/", "-")}-{number}',
                'external_ref': str(issue.get('html_url') or ''),
            })
            self.mark(number)
            queued.append(number)
        return queued
