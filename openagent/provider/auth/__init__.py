"""Auth for every mode: API keys, subscription OAuth, and local servers."""

from openagent.provider.auth.broker import API_KEY_PROVIDERS, AuthBroker, ApiKeyProvider
from openagent.provider.auth.flow import (
    AuthorizationRequest,
    LoopbackCallback,
    OAuthError,
    build_authorization_request,
    exchange_code,
    refresh_tokens,
    sign_in_interactive,
)
from openagent.provider.auth.pkce import code_challenge_s256, generate_code_verifier
from openagent.provider.auth.spec import (
    ANTHROPIC_OAUTH,
    ANTHROPIC_OAUTH_SPEC,
    OPENAI_OAUTH,
    OPENAI_OAUTH_SPEC,
    OAuthProviderSpec,
    all_specs,
    get_spec,
)
from openagent.provider.auth.tokens import (
    AuthRequired,
    AuthStatus,
    Credentials,
    TokenSet,
)

__all__ = [
    "API_KEY_PROVIDERS",
    "ANTHROPIC_OAUTH",
    "ANTHROPIC_OAUTH_SPEC",
    "AuthBroker",
    "AuthRequired",
    "AuthStatus",
    "ApiKeyProvider",
    "AuthorizationRequest",
    "Credentials",
    "LoopbackCallback",
    "OPENAI_OAUTH",
    "OPENAI_OAUTH_SPEC",
    "OAuthError",
    "OAuthProviderSpec",
    "TokenSet",
    "all_specs",
    "build_authorization_request",
    "code_challenge_s256",
    "exchange_code",
    "generate_code_verifier",
    "get_spec",
    "refresh_tokens",
    "sign_in_interactive",
]
