"""OAuth flow: PKCE, authorize URL, token exchange/refresh, loopback listener.

No network: token endpoints are mocked, the loopback listener is exercised with
a real socket on an ephemeral port.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit
import pytest

from openagent.provider.auth import flow
from openagent.provider.auth.flow import (
    LoopbackCallback,
    OAuthError,
    build_authorization_request,
    decode_jwt_claims,
    exchange_code,
    refresh_tokens,
    token_from_response,
)
from openagent.provider.auth.pkce import code_challenge_s256, generate_code_verifier
from openagent.provider.auth.spec import ANTHROPIC_OAUTH_SPEC, OPENAI_OAUTH_SPEC


# ----------------------------------------------------------------------- PKCE

def test_pkce_matches_the_rfc_7636_test_vector():
    """RFC 7636 Appendix B: the canonical verifier -> challenge pair."""
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert code_challenge_s256(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_generated_verifier_is_in_the_rfc_length_range():
    verifier = generate_code_verifier()
    assert 43 <= len(verifier) <= 128
    assert set(verifier) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


# -------------------------------------------------------------- authorize URL

def test_anthropic_authorize_url_carries_pkce_and_its_own_params():
    request = build_authorization_request(ANTHROPIC_OAUTH_SPEC)
    parts = urlsplit(request.authorize_url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}

    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://claude.ai/oauth/authorize"
    assert query["response_type"] == "code"
    assert query["code_challenge_method"] == "S256"
    assert query["code_challenge"] == code_challenge_s256(request.verifier)
    assert query["state"] == request.state
    assert query["redirect_uri"] == ANTHROPIC_OAUTH_SPEC.redirect_uri
    assert query["code"] == "true"  # Anthropic-specific
    assert "user:inference" in query["scope"]


def test_openai_authorize_url_carries_the_codex_parameters():
    request = build_authorization_request(OPENAI_OAUTH_SPEC)
    query = {k: v[0] for k, v in parse_qs(urlsplit(request.authorize_url).query).items()}
    assert query["client_id"] == OPENAI_OAUTH_SPEC.client_id
    assert query["audience"] == "https://api.openai.com/v1"
    assert query["originator"] == "codex_cli"
    assert "offline_access" in query["scope"]


# ------------------------------------------------------------ token responses

def test_anthropic_token_response_yields_account_and_expiry():
    tokens = token_from_response(
        ANTHROPIC_OAUTH_SPEC,
        {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 3600,
            "scope": "user:inference",
            "account": {"account_id": "acct_1", "email_address": "me@x.com"},
        },
    )
    assert tokens.access_token == "at"
    assert tokens.account_id == "acct_1"
    assert tokens.expires_at > time.time() + 3500


def test_openai_token_response_reads_the_account_id_from_the_jwt():
    """The ChatGPT account id lives in a namespaced JWT claim, and the
    inference call will not route without it."""
    payload = {
        "https://api.openai.com/auth": {
            "chatgpt_account_id": "acct_42",
            "plan_type": "plus",
        }
    }
    jwt = "h." + base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode() + ".s"
    tokens = token_from_response(OPENAI_OAUTH_SPEC, {"access_token": jwt, "refresh_token": "rt"})
    assert tokens.account_id == "acct_42"
    assert tokens.plan_type == "plus"


def test_missing_access_token_is_an_explicit_error():
    with pytest.raises(OAuthError, match="no access_token"):
        token_from_response(ANTHROPIC_OAUTH_SPEC, {"error": "invalid_grant"})


def test_decode_jwt_claims_tolerates_garbage():
    assert decode_jwt_claims("not-a-jwt") == {}


# --------------------------------------------------------- exchange / refresh

class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeClient:
    """Captures what the flow sends to the token endpoint."""

    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, data=None, headers=None):
        self.calls.append({"url": url, "json": json, "data": data, "headers": headers})
        return FakeResponse(self.payload, self.status)


@pytest.fixture
def patch_httpx(monkeypatch):
    def apply(payload, status=200):
        client = FakeClient(payload, status)
        monkeypatch.setattr(flow.httpx, "AsyncClient", lambda **kw: client)
        return client

    return apply


async def test_code_exchange_sends_the_verifier(patch_httpx):
    client = patch_httpx({"access_token": "at", "refresh_token": "rt", "expires_in": 3600})
    tokens = await exchange_code(ANTHROPIC_OAUTH_SPEC, "code-1", "state-1", "verifier-1", "http://localhost:53692/callback")
    assert tokens.access_token == "at"

    call = client.calls[0]
    assert call["url"] == ANTHROPIC_OAUTH_SPEC.token_url
    # Anthropic takes JSON; standard OAuth endpoints take form-encoded.
    assert call["json"]["grant_type"] == "authorization_code"
    assert call["json"]["code_verifier"] == "verifier-1"
    assert call["data"] is None


async def test_openai_exchange_is_form_encoded(patch_httpx):
    client = patch_httpx({"access_token": "at"})
    await exchange_code(OPENAI_OAUTH_SPEC, "code-1", "st", "v", "http://localhost:1455/auth/callback")
    call = client.calls[0]
    assert call["data"]["grant_type"] == "authorization_code"
    assert call["data"]["code_verifier"] == "v"
    assert call["json"] is None
    assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"


async def test_refresh_uses_the_refresh_grant(patch_httpx):
    client = patch_httpx({"access_token": "fresh", "expires_in": 3600})
    tokens = await refresh_tokens(ANTHROPIC_OAUTH_SPEC, "rt")
    assert tokens.access_token == "fresh"
    assert client.calls[0]["json"]["grant_type"] == "refresh_token"
    assert client.calls[0]["json"]["refresh_token"] == "rt"


async def test_http_error_surfaces_the_response_body(patch_httpx):
    patch_httpx({"error": {"message": "invalid client"}}, status=400)
    with pytest.raises(OAuthError, match="400"):
        await exchange_code(ANTHROPIC_OAUTH_SPEC, "c", "s", "v", "http://localhost:53692/callback")


# -------------------------------------------------------------- loopback server

async def test_loopback_bound_uri_reports_the_real_port():
    listener = LoopbackCallback("http://localhost:0/callback")
    uri = await listener.start()
    try:
        assert urlsplit(uri).port not in (None, 0)
        assert urlsplit(uri).path == "/callback"
    finally:
        await listener.stop()


async def test_loopback_captures_code_and_state():
    listener = LoopbackCallback("http://localhost:0/callback")
    uri = await listener.start()
    try:
        port = urlsplit(uri).port
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /callback?code=abc123&state=xyz HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer.drain()
        await reader.read(4096)
        writer.close()
        code, state = await listener.wait(timeout=5)
        assert code == "abc123"
        assert state == "xyz"
    finally:
        await listener.stop()


async def test_loopback_reports_a_denied_sign_in():
    listener = LoopbackCallback("http://localhost:0/callback")
    uri = await listener.start()
    try:
        port = urlsplit(uri).port
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /callback?error=access_denied HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer.drain()
        await reader.read(4096)
        writer.close()
        with pytest.raises(OAuthError, match="access_denied"):
            await listener.wait(timeout=5)
    finally:
        await listener.stop()


async def test_loopback_refuses_to_bind_a_public_interface():
    """A wildcard listener would let any host on the LAN collect the auth code."""
    listener = LoopbackCallback("http://0.0.0.0:1234/callback")
    with pytest.raises(OAuthError, match="loopback"):
        await listener.start()


async def test_loopback_times_out_when_nobody_calls_back():
    listener = LoopbackCallback("http://localhost:0/callback")
    await listener.start()
    try:
        with pytest.raises(OAuthError, match="Timed out"):
            await listener.wait(timeout=0.3)
    finally:
        await listener.stop()
