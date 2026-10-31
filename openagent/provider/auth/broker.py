"""AuthBroker: one place that turns a ModelRef into working credentials.

Storage is the macOS Keychain (via :class:`SecretManager`), so subscription
tokens and API keys never land in a plaintext file. Resolution order is
explicit and predictable:

1. Keychain entry for the provider (set by ``auth login``)
2. Environment variable (``ANTHROPIC_API_KEY`` etc.)
3. Nothing — raise :class:`AuthRequired` with a sign-in hint the UI can render

Expired subscription tokens are refreshed transparently and written back.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from openagent.core.config import SecretManager
from openagent.core.logging import logger
from openagent.provider.auth.spec import OAuthProviderSpec, all_specs
from openagent.provider.auth.tokens import (
    AuthRequired,
    AuthStatus,
    Credentials,
    TokenSet,
)
from openagent.provider.model_ref import AuthMode, ModelRef

_PREFIX = "auth:"


@dataclass(frozen=True)
class ApiKeyProvider:
    key: str
    label: str
    env_var: str
    wire_api: str
    base_url: str | None = None
    extra_env: tuple[str, ...] = ()


API_KEY_PROVIDERS: dict[str, ApiKeyProvider] = {
    p.key: p
    for p in (
        ApiKeyProvider("anthropic", "Anthropic API", "ANTHROPIC_API_KEY", "anthropic-messages"),
        ApiKeyProvider(
            "openai", "OpenAI API", "OPENAI_API_KEY", "openai-chat",
            extra_env=("OPENAI_ORG_ID", "OPENAI_PROJECT_ID"),
        ),
        ApiKeyProvider("gemini", "Google Gemini API", "GEMINI_API_KEY", "gemini"),
        ApiKeyProvider("openai_compatible", "OpenAI-compatible endpoint", "OPENAI_API_KEY", "openai-chat"),
    )
}


def _decode_payload(provider: str, raw: str | None) -> dict[str, Any] | None:
    """Parse a stored payload. ``None`` when there is none, or it will not parse."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            f"Credential entry for '{provider}' is unreadable; discarding it. "
            f"Sign in again with: auth login {provider}"
        )
        return None
    return data if isinstance(data, dict) else None


def _stored_headers(spec: OAuthProviderSpec, credentials: Credentials) -> dict[str, str]:
    """The headers worth persisting: not the ones the spec derives.

    ``credentials`` comes from ``resolve()``, so its headers already carry the
    spec's (vendor headers, the account id under the spec's header name).
    Saving those froze them into the keychain, where they override the spec
    from then on — a renamed account header kept sending the old name. Any
    header carrying the account id is derived, whatever it is called.
    """
    account_id = credentials.tokens.account_id if credentials.tokens else None
    return {
        name: value
        for name, value in credentials.headers.items()
        if name not in spec.api_headers and name != spec.account_header and not (account_id and value == account_id)
    }


class AuthBroker:
    """Credential resolution, storage, and refresh."""

    def __init__(
        self,
        store: SecretManager | None = None,
        env: dict[str, str] | None = None,
        *,
        refresher: Callable[[OAuthProviderSpec, str], Any] | None = None,
    ) -> None:
        # `is None`, not truthiness: a store that is empty (an empty dict-backed
        # fake in a test) is a valid store. `store or SecretManager()` made such
        # a fake fall through to the real macOS Keychain, so tests wrote there.
        self._store = SecretManager() if store is None else store
        self._env = os.environ if env is None else env
        # Injectable so tests never hit the network.
        self._refresher = refresher

    # ---------------------------------------------------------------- storage

    def _account(self, provider: str) -> str:
        return f"{_PREFIX}{provider}"

    def save_credentials(self, provider: str, credentials: Credentials) -> None:
        payload = {
            "mode": str(credentials.mode),
            "api_key": credentials.api_key,
            "headers": credentials.headers,
            "label": credentials.label,
            "tokens": credentials.tokens.to_dict() if credentials.tokens else None,
        }
        self._store.set_secret(self._account(provider), json.dumps(payload))

    def load_credentials(self, provider: str) -> Credentials | None:
        account = self._account(provider)
        raw = self._store.get_secret(account)
        data = _decode_payload(provider, raw)
        if data is None and raw:
            # An entry that will not parse is not a credential, and leaving it
            # where it is wedges the provider: the keychain is read first, so a
            # half-written item shadows the intact copy the file backend kept,
            # `auth status` says "not signed in" about a token that is on disk,
            # and the only way out is `auth logout`. Discard the unusable entry
            # and take the copy that survives it.
            data = _decode_payload(provider, self._store.quarantine_secret(account))
        if data is None:
            return None
        tokens = data.get("tokens")
        return Credentials(
            mode=AuthMode(data.get("mode", AuthMode.API_KEY)),
            api_key=data.get("api_key"),
            tokens=TokenSet.from_dict(tokens) if tokens else None,
            headers=data.get("headers") or {},
            label=data.get("label", ""),
            source="keychain",
        )

    def delete_credentials(self, provider: str) -> bool:
        return self._store.delete_secret(self._account(provider))

    # ------------------------------------------------------------- resolution

    def resolve(self, ref: ModelRef) -> Credentials:
        """Return credentials for ``ref`` or raise :class:`AuthRequired`."""
        if ref.auth_mode is AuthMode.LOCAL:
            return Credentials(mode=AuthMode.LOCAL, source="none")

        stored = self.load_credentials(ref.provider)
        if stored is not None and stored.mode is ref.auth_mode:
            return self._with_spec_headers(ref, stored)

        if ref.auth_mode is AuthMode.API_KEY:
            from_env = self._from_env(ref.provider)
            if from_env is not None:
                return self._with_spec_headers(ref, from_env)

        raise AuthRequired(
            ref.provider,
            (
                f"No credential for {ref.display}. "
                f"Run `agent auth login {ref.provider}` (subscription) or set an API key."
            ),
            sign_in_hint=ref.provider,
        )

    def _from_env(self, provider: str) -> Credentials | None:
        spec = API_KEY_PROVIDERS.get(provider)
        if spec is None:
            return None
        key = self._env.get(spec.env_var)
        if not key:
            return None
        headers: dict[str, str] = {}
        for name in spec.extra_env:
            value = self._env.get(name)
            if value and name == "OPENAI_ORG_ID":
                headers["OpenAI-Organization"] = value
            if value and name == "OPENAI_PROJECT_ID":
                headers["OpenAI-Project"] = value
        return Credentials(
            mode=AuthMode.API_KEY, api_key=key, headers=headers, source="env", label=spec.label
        )

    def _with_spec_headers(self, ref: ModelRef, credentials: Credentials) -> Credentials:
        """Merge vendor-required headers (e.g. Anthropic's OAuth beta flag)."""
        spec = self._spec_for(ref)
        if spec is None:
            return credentials
        merged = {**spec.api_headers, **credentials.headers}
        if credentials.tokens and credentials.tokens.account_id and spec.account_header:
            merged[spec.account_header] = credentials.tokens.account_id
        return Credentials(
            mode=credentials.mode,
            api_key=credentials.api_key,
            tokens=credentials.tokens,
            headers=merged,
            source=credentials.source,
            label=credentials.label,
        )

    @staticmethod
    def _spec_for(ref: ModelRef) -> OAuthProviderSpec | None:
        """Find the OAuth spec for a ref.

        The provider key *is* the spec key (``anthropic_oauth``). Accepting the
        bare vendor name too would let an ``anthropic`` API key be treated as a
        subscription credential, so it is deliberately not accepted here.
        """
        if ref.auth_mode is not AuthMode.OAUTH_SUBSCRIPTION:
            return None
        return all_specs().get(ref.provider)

    # ---------------------------------------------------------------- refresh

    async def refresh_if_needed(self, ref: ModelRef, *, force: bool = False) -> Credentials:
        """Refresh a subscription token that is about to expire."""
        credentials = self.resolve(ref)
        tokens = credentials.tokens
        if tokens is None:
            return credentials
        if not (force or tokens.expires_within()):
            return credentials
        if not tokens.refresh_token:
            raise AuthRequired(
                ref.provider,
                f"{ref.display}: session expired and no refresh token — sign in again.",
                sign_in_hint=ref.provider,
            )

        spec = self._spec_for(ref)
        if spec is None:
            return credentials

        refresher = self._refresher
        if refresher is None:
            from openagent.provider.auth.flow import refresh_tokens

            async def refresher(spec_: OAuthProviderSpec, token: str) -> TokenSet:
                return await refresh_tokens(spec_, token)

        refreshed = await refresher(spec, tokens.refresh_token)
        # A refresh may not return a new refresh token; keep the old one.
        refreshed.refresh_token = refreshed.refresh_token or tokens.refresh_token
        updated = Credentials(
            mode=credentials.mode,
            api_key=credentials.api_key,
            tokens=refreshed,
            headers=_stored_headers(spec, credentials),
            source="oauth",
            label=credentials.label,
        )
        self.save_credentials(ref.provider, updated)
        logger.info(f"Refreshed {ref.provider} subscription token")
        return self._with_spec_headers(ref, updated)

    # ----------------------------------------------------------------- status

    def status(self) -> list[AuthStatus]:
        """Everything the settings screen shows, in a stable order."""
        rows: list[AuthStatus] = []

        for provider in API_KEY_PROVIDERS.values():
            stored = self.load_credentials(provider.key)
            from_env = self._env.get(provider.env_var)
            configured = bool((stored and stored.api_key) or from_env)
            rows.append(
                AuthStatus(
                    provider=provider.key,
                    label=provider.label,
                    mode=AuthMode.API_KEY,
                    configured=configured,
                    source="keychain" if (stored and stored.api_key) else ("env" if from_env else ""),
                )
            )

        for key, spec in all_specs().items():
            # Keyed by the spec key (``anthropic_oauth``), which is what
            # ``resolve()`` reads for a subscription ModelRef. Keeping the API
            # key under the bare vendor name (``anthropic``) is what stops a
            # subscription sign-in from overwriting an API key and vice versa.
            stored = self.load_credentials(key)
            tokens = stored.tokens if stored else None
            rows.append(
                AuthStatus(
                    provider=key,
                    label=spec.label,
                    mode=AuthMode.OAUTH_SUBSCRIPTION,
                    configured=bool(tokens),
                    source="keychain" if tokens else "",
                    account=(tokens.account_id or "") if tokens else "",
                    plan=(tokens.plan_type or "") if tokens else "",
                    expires_at=tokens.expires_at if tokens else None,
                    needs_refresh=bool(tokens and tokens.expires_within()),
                    unofficial=spec.unofficial,
                )
            )

        return rows

    # Convenience for the CLI ------------------------------------------------

    def save_api_key(self, provider: str, api_key: str, label: str = "") -> None:
        self.save_credentials(
            provider,
            Credentials(mode=AuthMode.API_KEY, api_key=api_key, label=label, source="keychain"),
        )

    def save_tokens(self, provider: str, tokens: TokenSet, label: str = "") -> Credentials:
        """Store subscription tokens under the provider's spec key.

        ``provider`` must be the spec key (``anthropic_oauth``), not the bare
        vendor name — that is the key ``resolve()`` reads for a subscription
        ModelRef, and the key that keeps it separate from an API key.
        """
        credentials = Credentials(
            mode=AuthMode.OAUTH_SUBSCRIPTION, tokens=tokens, label=label, source="oauth"
        )
        self.save_credentials(provider, credentials)
        return self._with_spec_headers(
            ModelRef(
                model="-",
                provider=provider,
                wire_api="anthropic-messages",
                auth_mode=AuthMode.OAUTH_SUBSCRIPTION,
            ),
            credentials,
        )
