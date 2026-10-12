"""Credential types shared by every auth mode."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from openagent.provider.model_ref import AuthMode


class AuthRequired(RuntimeError):
    """No usable credential for a model that needs one.

    Carries enough context for the Mac app to render a sign-in prompt.
    """

    def __init__(self, provider: str, message: str, sign_in_hint: str = "") -> None:
        super().__init__(message)
        self.provider = provider
        self.sign_in_hint = sign_in_hint


@dataclass
class TokenSet:
    """OAuth tokens for a subscription login."""

    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None          # unix timestamp
    scope: str | None = None
    account_id: str | None = None
    plan_type: str | None = None
    id_token: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def expires_within(self, seconds: int = 120) -> bool:
        if self.expires_at is None:
            return False
        return time.time() + seconds >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
            "account_id": self.account_id,
            "plan_type": self.plan_type,
            "id_token": self.id_token,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenSet:
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=data.get("expires_at"),
            scope=data.get("scope"),
            account_id=data.get("account_id"),
            plan_type=data.get("plan_type"),
            id_token=data.get("id_token"),
            extra=data.get("extra") or {},
        )


@dataclass
class Credentials:
    """Everything needed to authenticate calls for one provider."""

    mode: AuthMode
    api_key: str | None = None
    tokens: TokenSet | None = None
    headers: dict[str, str] = field(default_factory=dict)
    source: str = ""            # "keychain" | "env" | "oauth" | "none"
    label: str = ""

    @property
    def bearer_token(self) -> str | None:
        return self.tokens.access_token if self.tokens else None

    @property
    def is_expired(self) -> bool:
        return bool(self.tokens and self.tokens.expires_within())


@dataclass
class AuthStatus:
    """What the settings UI shows per provider."""

    provider: str
    label: str
    mode: AuthMode
    configured: bool
    source: str = ""
    account: str = ""
    plan: str = ""
    expires_at: float | None = None
    needs_refresh: bool = False
    unofficial: bool = False    # subscription OAuth is not an official integration
