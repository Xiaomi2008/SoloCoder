"""RFC 7636 PKCE helpers for the authorization-code flow."""

from __future__ import annotations

import base64
import hashlib
import secrets

# RFC 7636 §4.1: verifier is 43..128 chars of [A-Za-z0-9-._~]
_VERIFIER_BYTES = 48  # -> 64 base64url chars, comfortably in range


def generate_code_verifier() -> str:
    return secrets.token_urlsafe(_VERIFIER_BYTES)[:128]


def code_challenge_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_state() -> str:
    return secrets.token_urlsafe(32)
