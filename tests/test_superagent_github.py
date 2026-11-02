"""GitHub Issues polling: label gate, PR exclusion, dedupe, untrusted body text."""
import json

import httpx
import pytest

from openagent.apps.superagent.github import GitHubIssues
from openagent.apps.superagent.service import Inbox


class FakeService:
    def __init__(self, tmp_path):
        self.inbox = Inbox(str(tmp_path))
        self.queued = []

    def accept(self, data):
        self.queued.append(data)
        return self.inbox.accept(data)


def issues_handler(issues, token_ok='Bearer t'):
    def handler(request: httpx.Request):
        assert request.headers['authorization'] == token_ok
        assert request.url.params['labels'] == 'agent'
        assert request.url.params['state'] == 'open'
        return httpx.Response(200, json=issues)
    return handler


def make(tmp_path, handler):
    state = tmp_path / 'gh.json'
    connector = GitHubIssues('o/r', 'agent', token='t', state_file=state)
    transport = httpx.MockTransport(handler)
    return connector, state, transport


async def test_poll_queues_labeled_issue_and_dedupes(tmp_path, monkeypatch):
    issues = [
        {'number': 1, 'title': 'Build game', 'body': 'One file.', 'html_url': 'u1'},
        {'number': 2, 'title': 'A PR', 'body': None, 'pull_request': {'url': 'p'}},
    ]
    connector, state, transport = make(tmp_path, issues_handler(issues))
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient',
                        lambda **kw: original(transport=transport, **kw))
    service = FakeService(tmp_path)
    assert await connector.poll(service) == [1]
    assert service.queued[0]['text'] == 'Build game\n\nOne file.'
    assert service.queued[0]['source'] == 'github'
    assert json.loads(state.read_text()) == [1]
    assert await connector.poll(service) == []


async def test_title_only_and_empty_title_skipped(tmp_path, monkeypatch):
    issues = [{'number': 3, 'title': 'Do it', 'body': ''},
              {'number': 4, 'title': '   ', 'body': 'x'}]
    connector, _, transport = make(tmp_path, issues_handler(issues))
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=transport, **kw))
    service = FakeService(tmp_path)
    assert await connector.poll(service) == [3]
    assert service.queued[0]['text'] == 'Do it'


async def test_http_error_propagates(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(401, json={'message': 'bad credentials'})
    connector, _, transport = make(tmp_path, handler)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=transport, **kw))
    with pytest.raises(httpx.HTTPStatusError):
        await connector.poll(FakeService(tmp_path))


@pytest.mark.parametrize('repo', ['nodash', 'a/b/c'])
def test_repo_format_validated(tmp_path, repo):
    with pytest.raises(ValueError):
        GitHubIssues(repo, 'agent', state_file=tmp_path / 'x')


def test_token_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'env-token')
    connector = GitHubIssues('o/r', 'agent', state_file=tmp_path / 'x')
    assert connector.token == 'env-token'


def test_corrupt_state_starts_empty(tmp_path):
    path = tmp_path / 'gh.json'
    path.write_text('{bad')
    connector = GitHubIssues('o/r', 'agent', token='t', state_file=path)
    assert connector._seen == set()
