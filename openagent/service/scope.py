"""Session scope: the rule that decides what a relative path means.

The model writes paths; the *session* decides where they land. A general agent
app with no scope is an agent that writes into the .app bundle, and a service
that accepts any path from any client is an agent that writes into `/`. Both
are refused here, in one place, before a tool ever runs.
"""

from __future__ import annotations

from pathlib import Path

from openagent.service.protocol import ErrorCode, RpcError

#: Refusing these is not paranoia about the user — the app's own directory is
#: the one a sidecar will pick by accident, and `/` is the one a bug reaches.
ALWAYS_REFUSED: tuple[str, ...] = ("/",)


def validate_working_dir(raw: object, roots: tuple[str, ...] = ()) -> Path:
    """Absolute, existing, allowed — or ``WORKING_DIR_DENIED``.

    ``roots`` is the server's allow-list. Empty means "any absolute path",
    which is what a developer machine wants; a shipped app passes the folders
    the user has granted (and macOS TCC is the layer above that).
    """
    if not isinstance(raw, str) or not raw.strip():
        raise RpcError(
            ErrorCode.WORKING_DIR_DENIED,
            "working_dir is required: an absolute path to the project directory",
        )

    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise RpcError(
            ErrorCode.WORKING_DIR_DENIED,
            f"working_dir must be an absolute path, got {raw!r}",
            {"working_dir": raw},
        )

    resolved = candidate.resolve()
    if str(resolved) in ALWAYS_REFUSED:
        raise RpcError(
            ErrorCode.WORKING_DIR_DENIED,
            "refusing to scope a run to the filesystem root; pick a project directory",
            {"working_dir": str(resolved)},
        )

    if not resolved.exists():
        raise RpcError(
            ErrorCode.WORKING_DIR_DENIED,
            f"{resolved} does not exist",
            {"working_dir": str(resolved)},
        )
    if not resolved.is_dir():
        raise RpcError(
            ErrorCode.WORKING_DIR_DENIED,
            f"{resolved} is not a directory",
            {"working_dir": str(resolved)},
        )

    if roots:
        allowed = [Path(r).expanduser().resolve() for r in roots]
        if not any(resolved == root or resolved.is_relative_to(root) for root in allowed):
            raise RpcError(
                ErrorCode.WORKING_DIR_DENIED,
                f"{resolved} is outside the directories this server serves",
                {"working_dir": str(resolved), "roots": [str(a) for a in allowed]},
            )

    return resolved


__all__ = ["ALWAYS_REFUSED", "validate_working_dir"]
