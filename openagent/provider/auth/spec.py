"""OAuth endpoint specs for subscription sign-in.

These endpoints, client ids, scopes and redirect ports are the ones the
vendors' own CLIs use. They are **not** a public, documented third-party
integration: Anthropic and OpenAI issue these tokens to their own clients.

Consequences we design for:
- The values can change without notice, so every one of them is a constant on a
  spec object that can be overridden from config/env — a vendor change is a
  one-line patch, not a refactor.
- The UI labels this path "unofficial, at your own risk" (see AuthStatus.unofficial).
- API-key auth remains the first-class, supported path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Any

ANTHROPIC_OAUTH = "anthropic_oauth"
OPENAI_OAUTH = "openai_oauth"


@dataclass(frozen=True)
class OAuthProviderSpec:
    key: str
    label: str

    authorize_url: str
    token_url: str
    client_id: str
    scope: str
    redirect_uri: str

    # Where inference calls go once authenticated. For ChatGPT subscriptions
    # this is NOT api.openai.com — the platform endpoint rejects subscription
    # JWTs with 401; only the Codex backend accepts them.
    api_base_url: str
    wire_api: str = "anthropic-messages"

    # Extra query params on the authorize URL.
    authorize_params: dict[str, str] = field(default_factory=dict)
    # Extra form fields on the token request.
    token_params: dict[str, str] = field(default_factory=dict)
    # "json" (Anthropic) or "form" (standard OAuth, OpenAI).
    token_encoding: str = "form"
    # Headers required on inference calls with an OAuth-derived token.
    api_headers: dict[str, str] = field(default_factory=dict)
    # JWT claim path carrying the account id, e.g. ("https://api.openai.com/auth",
    # "chatgpt_account_id"). A tuple, because the claim key is itself a URL with dots.
    account_claim: tuple[str, ...] | None = None
    # Header used to pass the account id, if any.
    account_header: str | None = None

    unofficial: bool = True

    def env_overrides(self) -> OAuthProviderSpec:
        """Apply ``OPENAGENT_<KEY>_<FIELD>`` overrides.

        Lets a user repair a broken integration without a code change, e.g.
        ``OPENAGENT_ANTHROPIC_OAUTH_CLIENT_ID=...``.
        """
        prefix = f"OPENAGENT_{self.key.upper()}_"
        changes: dict[str, Any] = {}
        for name in (
            "authorize_url", "token_url", "client_id", "scope",
            "redirect_uri", "api_base_url",
        ):
            value = os.environ.get(f"{prefix}{name.upper()}")
            if value:
                changes[name] = value
        return replace(self, **changes) if changes else self


# Claude Pro/Max, as used by the Claude Code CLI.
ANTHROPIC_OAUTH_SPEC = OAuthProviderSpec(
    key=ANTHROPIC_OAUTH,
    label="Claude Pro / Max",
    authorize_url="https://claude.ai/oauth/authorize",
    token_url="https://console.anthropic.com/v1/oauth/token",
    client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
    # Least privilege that still unlocks inference.
    scope="org:create_api_key user:profile user:inference",
    redirect_uri="http://localhost:53692/callback",
    api_base_url="https://api.anthropic.com",
    wire_api="anthropic-messages",
    authorize_params={"code": "true"},
    token_encoding="json",
    # Required for Bearer tokens on the Messages API.
    api_headers={"anthropic-beta": "oauth-2025-04-20"},
    account_claim=None,
    account_header="anthropic-account-id",
)

# ChatGPT Plus/Pro, as used by the Codex CLI.
OPENAI_OAUTH_SPEC = OAuthProviderSpec(
    key=OPENAI_OAUTH,
    label="ChatGPT Plus / Pro",
    authorize_url="https://auth.openai.com/oauth/authorize",
    token_url="https://auth.openai.com/oauth/token",
    client_id="app_EMoamEEZ73f0CkXaXp7hrann",
    scope="openid profile email offline_access",
    redirect_uri="http://localhost:1455/auth/callback",
    # Subscription tokens are only accepted by the Codex Responses backend.
    api_base_url="https://chatgpt.com/backend-api/codex",
    wire_api="openai-responses",
    authorize_params={
        "audience": "https://api.openai.com/v1",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "codex_cli",
    },
    api_headers={"Originator": "codex_cli"},
    account_claim=("https://api.openai.com/auth", "chatgpt_account_id"),
    # What the Codex CLI sends; the backend has accepted OpenAI-Organization
    # too, but matching the vendor's client is what survives a stricter check.
    account_header="chatgpt-account-id",
)


def all_specs() -> dict[str, OAuthProviderSpec]:
    return {
        ANTHROPIC_OAUTH: ANTHROPIC_OAUTH_SPEC.env_overrides(),
        OPENAI_OAUTH: OPENAI_OAUTH_SPEC.env_overrides(),
    }


def get_spec(key: str) -> OAuthProviderSpec:
    specs = all_specs()
    if key not in specs:
        raise KeyError(f"Unknown OAuth provider '{key}'. Known: {', '.join(sorted(specs))}")
    return specs[key]
