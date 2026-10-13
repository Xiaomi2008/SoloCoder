"""Authorization-code + PKCE flow, and token exchange/refresh.

The CLI runs the whole flow (loopback listener + browser). The Mac app runs the
authorize step itself with ``ASWebAuthenticationSession`` and hands the code to
:func:`exchange_code`, so both share one implementation of the wire protocol.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx

from openagent.core.logging import logger
from openagent.provider.auth.pkce import (
    code_challenge_s256,
    generate_code_verifier,
    generate_state,
)
from openagent.provider.auth.spec import OAuthProviderSpec
from openagent.provider.auth.tokens import TokenSet

CALLBACK_TIMEOUT_SECONDS = 300
_SUCCESS_PAGE = (
    b"<html><body style='font-family:system-ui'>"
    b"<h2>Signed in</h2><p>You can close this window and return to the app.</p>"
    b"</body></html>"
)
_FAILURE_PAGE = (
    b"<html><body style='font-family:system-ui'>"
    b"<h2>Sign-in failed</h2><p>The app reported an error; see its window.</p>"
    b"</body></html>"
)


class OAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthorizationRequest:
    authorize_url: str
    verifier: str
    state: str
    redirect_uri: str


def build_authorization_request(
    spec: OAuthProviderSpec, redirect_uri: str | None = None
) -> AuthorizationRequest:
    """Compose the authorize URL with PKCE challenge and state."""
    verifier = generate_code_verifier()
    state = generate_state()
    uri = redirect_uri or spec.redirect_uri

    params = {
        "response_type": "code",
        "client_id": spec.client_id,
        "redirect_uri": uri,
        "scope": spec.scope,
        "state": state,
        "code_challenge": code_challenge_s256(verifier),
        "code_challenge_method": "S256",
        **spec.authorize_params,
    }

    parts = urlsplit(spec.authorize_url)
    existing = dict(parse_qs(parts.query))
    merged = {k: v for k, v in existing.items() if k not in params}
    query = urlencode({**merged, **params})
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))

    return AuthorizationRequest(authorize_url=url, verifier=verifier, state=state, redirect_uri=uri)


def decode_jwt_claims(jwt: str) -> dict:
    """Decode a JWT payload without verifying the signature.

    Used only to read the account id the token exchange already gave us over a
    TLS channel; we never make trust decisions from these claims.
    """
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except Exception:
        return {}


def _lookup(claims: dict, path: tuple[str, ...]) -> str | None:
    node = claims
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, str) else None


def token_from_response(spec: OAuthProviderSpec, payload: dict) -> TokenSet:
    """Normalize a token-endpoint response into a :class:`TokenSet`."""
    access_token = payload.get("access_token")
    if not access_token:
        raise OAuthError(
            f"{spec.label} token response had no access_token: "
            f"{json.dumps(payload)[:300]}"
        )

    expires_in = payload.get("expires_in")
    expires_at = time.time() + float(expires_in) if expires_in else None

    account_id = None
    account = payload.get("account")
    if isinstance(account, dict):
        candidate = account.get("account_id") or account.get("id")
        if isinstance(candidate, str):
            account_id = candidate

    plan_type = None
    if spec.account_claim:
        claims = decode_jwt_claims(access_token)
        account_id = account_id or _lookup(claims, spec.account_claim)
        auth_claim = claims.get("https://api.openai.com/auth")
        if isinstance(auth_claim, dict):
            # OpenAI spells this claim `chatgpt_plan_type`; the Anthropic-style
            # claim is `plan_type`. Reading only the second left the plan blank
            # for every ChatGPT sign-in, and the plan is the one line that
            # explains a backend that refuses every model for the account.
            for key in ("chatgpt_plan_type", "plan_type"):
                if isinstance(auth_claim.get(key), str):
                    plan_type = auth_claim[key]
                    break

    return TokenSet(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_at=expires_at,
        scope=payload.get("scope"),
        account_id=account_id,
        plan_type=plan_type,
        id_token=payload.get("id_token"),
    )


async def _post_token(spec: OAuthProviderSpec, data: dict) -> dict:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        if spec.token_encoding == "json":
            response = await client.post(spec.token_url, json=data, headers=headers)
        else:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            response = await client.post(spec.token_url, data=data, headers=headers)

    if response.status_code >= 400:
        raise OAuthError(
            f"{spec.label} token request failed ({response.status_code}): "
            f"{response.text[:400]}"
        )
    return response.json()


async def exchange_code(
    spec: OAuthProviderSpec, code: str, state: str, verifier: str, redirect_uri: str
) -> TokenSet:
    """Swap an authorization code for tokens."""
    payload = await _post_token(
        spec,
        {
            "grant_type": "authorization_code",
            "code": code,
            "state": state,
            "client_id": spec.client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            **spec.token_params,
        },
    )
    return token_from_response(spec, payload)


async def refresh_tokens(spec: OAuthProviderSpec, refresh_token: str) -> TokenSet:
    """Refresh an expired access token."""
    payload = await _post_token(
        spec,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": spec.client_id,
            **spec.token_params,
        },
    )
    return token_from_response(spec, payload)


class LoopbackCallback:
    """Single-shot loopback HTTP server that captures the redirect.

    Binds the port from the spec's redirect_uri. Loopback redirect URIs are
    registered per-port by the vendors, so the port is not free choice.

    Binds **loopback only**, on both IPv4 and IPv6: ``localhost`` resolves to
    ``::1`` on macOS, so an IPv4-only listener breaks the browser redirect, and
    binding a wildcard interface would let any host on the LAN collect the auth
    code.
    """

    _LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")

    def __init__(self, redirect_uri: str) -> None:
        parts = urlsplit(redirect_uri)
        self.host = parts.hostname or "localhost"
        self.port = parts.port or 0
        self.path = parts.path or "/callback"
        self._server: asyncio.Server | None = None
        self._extra: asyncio.Server | None = None
        self._code: str | None = None
        self._state: str | None = None
        self._error: str | None = None

    @property
    def bound_redirect_uri(self) -> str:
        assert self._server is not None
        port = self._server.sockets[0].getsockname()[1]
        return f"http://localhost:{port}{self.path}"

    async def start(self) -> str:
        if self.host not in self._LOOPBACK_HOSTS:
            raise OAuthError(
                f"Refusing to listen on '{self.host}': the OAuth callback "
                "listener must be loopback, or the auth code can be intercepted."
            )

        port = self.port
        if port == 0:
            # Pick one free port, then bind both families to it. Binding two
            # sockets with port 0 would yield two *different* ports, and the
            # redirect URI can only name one.
            probe = await asyncio.start_server(self._handle, "127.0.0.1", 0)
            port = probe.sockets[0].getsockname()[1]
            probe.close()
            await probe.wait_closed()

        self._server = await asyncio.start_server(self._handle, "127.0.0.1", port)
        # Best effort: browsers may resolve `localhost` to ::1.
        try:
            self._extra = await asyncio.start_server(self._handle, "::1", port)
        except OSError:  # pragma: no cover - IPv6-less environments
            self._extra = None
        return self.bound_redirect_uri

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request_line = (await reader.readline()).decode("latin-1")
            # Drain headers so the browser sees a clean response.
            while True:
                line = await reader.readline()
                if not line or line in (b"\r\n", b"\n"):
                    break

            target = request_line.split(" ")[1] if len(request_line.split(" ")) > 1 else "/"
            query = parse_qs(urlsplit(target).query)

            if "code" in query:
                self._code = query["code"][0]
                self._state = query.get("state", [""])[0]
                body = _SUCCESS_PAGE
            else:
                self._error = query.get("error", ["user denied"])[0]
                body = _FAILURE_PAGE

            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except Exception as exc:  # pragma: no cover - socket plumbing
            logger.debug(f"Loopback callback handler error: {exc}")
        finally:
            writer.close()

    async def wait(self, timeout: float = CALLBACK_TIMEOUT_SECONDS) -> tuple[str, str]:
        """Wait for the redirect. Returns ``(code, state)``."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._code is not None:
                return self._code, self._state or ""
            if self._error is not None:
                raise OAuthError(f"Authorization failed: {self._error}")
            await asyncio.sleep(0.1)
        raise OAuthError("Timed out waiting for the browser sign-in to complete")

    async def stop(self) -> None:
        for server in (self._server, self._extra):
            if server is not None:
                server.close()
                try:
                    await server.wait_closed()
                except Exception:  # pragma: no cover - socket plumbing
                    pass
        self._server = None
        self._extra = None


async def sign_in_interactive(
    spec: OAuthProviderSpec,
    *,
    open_browser: bool = True,
    timeout: float = CALLBACK_TIMEOUT_SECONDS,
) -> TokenSet:
    """Full CLI sign-in: loopback listener, browser, code exchange."""
    import webbrowser

    listener = LoopbackCallback(spec.redirect_uri)
    try:
        redirect_uri = await listener.start()
    except OSError as exc:
        raise OAuthError(
            f"Could not listen on {spec.redirect_uri} ({exc}). "
            "The redirect port is fixed by the vendor; close the app holding it and retry."
        ) from exc

    request = build_authorization_request(spec, redirect_uri)
    print(f"\nOpen this URL to sign in to {spec.label}:\n\n  {request.authorize_url}\n")
    if open_browser:
        try:
            webbrowser.open(request.authorize_url)
        except Exception:
            pass

    try:
        code, state = await listener.wait(timeout)
    finally:
        await listener.stop()

    if state and state != request.state:
        raise OAuthError("Sign-in state mismatch — possible CSRF, aborting.")

    return await exchange_code(spec, code, state, request.verifier, redirect_uri)
